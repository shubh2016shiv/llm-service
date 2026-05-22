"""
Local Infrastructure Manager CLI.

Architecture:
-------------
    Developer terminal command
            |
            v
    LocalInfrastructureManager (orchestration layer)
            |
            v
    DockerComposeClient (execution adapter)
            |
            +-- postgres container (relational storage)
            +-- redis container (cache/counters)
            +-- vault container (local secret backend)
            +-- vault-init one-shot bootstrap container

Why this script exists:
    Local development needs repeatable infrastructure setup without manual
    Docker commands. This script centralizes startup order, readiness checks,
    schema application, and local secret defaults so every developer gets a
    consistent environment.

Step-by-step start flow:
    1. Ensure `.env` has required values (generate missing local secrets).
    2. Start infrastructure containers with Docker Compose.
    3. Wait until Postgres, Redis, and Vault report ready.
    4. Bootstrap Vault via `vault-init`.
    5. Apply PostgreSQL schema files in approved manifest order.
    6. Print connection details for local tools.

Secrets rationale:
    Generated values are intentionally local-only and gitignored through `.env`.
    They reduce accidental credential reuse across machines and avoid hardcoded
    static passwords in source control.

Example:
    python infrastructure/manage_local_infrastructure.py start
    python infrastructure/manage_local_infrastructure.py status
    python infrastructure/manage_local_infrastructure.py connect postgres

Dependencies:
    - docker-compose.yml: local service definitions and named volumes.
    - postgres_schema/schema_creation_order.md: approved SQL creation order.

Author: Shubham Singh
"""

from __future__ import annotations

import argparse
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


ROOT_DIRECTORY = Path(__file__).resolve().parents[1]
ENV_FILE_PATH = ROOT_DIRECTORY / ".env"
COMPOSE_FILE_PATH = ROOT_DIRECTORY / "docker-compose.yml"
SCHEMA_ORDER_PATH = ROOT_DIRECTORY / "postgres_schema" / "schema_creation_order.md"
SCHEMA_DIRECTORY = ROOT_DIRECTORY / "postgres_schema"

INFRASTRUCTURE_SERVICES = ("vault", "postgres", "redis")
REQUIRED_ENVIRONMENT_DEFAULTS: Mapping[str, str] = {
    "APP_ENVIRONMENT": "development",
    "POSTGRES_USER": "llm_user",
    "POSTGRES_DB": "llm_services",
    "POSTGRES_PASSWORD": f"local-postgres-{secrets.token_urlsafe(18)}",
    "REDIS_PASSWORD": f"local-redis-{secrets.token_urlsafe(18)}",
    "SECRET_BACKEND": "vault",
    "VAULT_ADDR": "http://localhost:8200",
    "VAULT_ROOT_TOKEN": f"local-vault-root-{secrets.token_urlsafe(18)}",
    "VAULT_SERVICE_USERNAME": "llm-service",
    "VAULT_SERVICE_PASSWORD": f"local-vault-service-{secrets.token_urlsafe(18)}",
    "VAULT_MOUNT_PATH": "secret",
    "VAULT_KV_PREFIX": "llm-provider-service",
}


@dataclass(frozen=True)
class LocalInfrastructureEnvironment:
    """Resolved connection and credential values for local containers.

    This value object keeps all runtime connection values in one place so
    startup, status, schema, and connect operations use the same resolved data.

    Example:
        postgres_url -> postgresql+asyncpg://<user>:<password>@localhost:5432/<db>
        redis_url    -> redis://:<password>@localhost:6379/0
    """

    postgres_user: str
    postgres_password: str
    postgres_database: str
    redis_password: str
    vault_root_token: str
    vault_service_username: str
    vault_service_password: str
    vault_kv_prefix: str

    @property
    def postgres_url(self) -> str:
        """Return host-side async PostgreSQL URL used by application services."""
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@localhost:5432/{self.postgres_database}"
        )

    @property
    def redis_url(self) -> str:
        """Return host-side Redis URL used by local cache-aware tools."""
        return f"redis://:{self.redis_password}@localhost:6379/0"


class CommandFailedError(RuntimeError):
    """Raised when a required Docker command exits with a non-zero code."""


class LocalEnvironmentFile:
    """Read and update local `.env` values required for infrastructure startup.

    Responsibility:
        - Preserve existing developer values.
        - Append only missing keys.
        - Derive convenience variables (`DATABASE_URL`, `REDIS_URL`) from
          primary values to keep runtime config consistent.
    """

    def __init__(self, env_file_path: Path) -> None:
        """Initialize with the `.env` file path to manage."""
        self._env_file_path = env_file_path

    def ensure_required_values(self) -> Mapping[str, str]:
        """Ensure required and derived local environment keys exist.

        Existing values are never overwritten. Missing values are appended.

        Returns:
            Mapping[str, str]: Complete resolved key-value view after updates.
        """
        current_values = self.read_values()
        missing_values = self._missing_values(current_values)
        if missing_values:
            self._append_values(missing_values)
            current_values = self.read_values()
        missing_derived_values = self._missing_derived_values(current_values)
        if missing_derived_values:
            self._append_values(missing_derived_values)
            current_values = self.read_values()
        return current_values

    def read_values(self) -> dict[str, str]:
        """Read `KEY=value` pairs from `.env` with lightweight parsing rules.

        Returns:
            dict[str, str]: Parsed environment values. Empty dict if `.env`
                does not yet exist.
        """
        if not self._env_file_path.exists():
            return {}
        return {
            key: value
            for key, value in (
                self._parse_line(line) for line in self._env_file_path.read_text().splitlines()
            )
            if key
        }

    def _missing_values(self, current_values: Mapping[str, str]) -> dict[str, str]:
        """Return required keys not currently present in `.env`."""
        return {
            key: value
            for key, value in REQUIRED_ENVIRONMENT_DEFAULTS.items()
            if not current_values.get(key)
        }

    def _missing_derived_values(self, current_values: Mapping[str, str]) -> dict[str, str]:
        """Return derived convenience keys that are missing."""
        derived_values = self._derived_values(current_values)
        return {key: value for key, value in derived_values.items() if not current_values.get(key)}

    def _derived_values(self, current_values: Mapping[str, str]) -> Mapping[str, str]:
        """Build derived URLs and Vault alias values from primary credentials.

        Rationale:
            Application code and scripts often consume `DATABASE_URL` and
            `REDIS_URL` directly. Deriving them here avoids drift between
            base credentials and composed URLs.
        """
        postgres_user = current_values["POSTGRES_USER"]
        postgres_password = current_values["POSTGRES_PASSWORD"]
        postgres_database = current_values["POSTGRES_DB"]
        redis_password = current_values["REDIS_PASSWORD"]
        return {
            "DATABASE_URL": (
                f"postgresql+asyncpg://{postgres_user}:{postgres_password}"
                f"@localhost:5432/{postgres_database}"
            ),
            "REDIS_URL": f"redis://:{redis_password}@localhost:6379/0",
            "VAULT_USERNAME": current_values["VAULT_SERVICE_USERNAME"],
            "VAULT_PASSWORD": current_values["VAULT_SERVICE_PASSWORD"],
        }

    def _append_values(self, missing_values: Mapping[str, str]) -> None:
        """Append missing keys to `.env` with a managed section comment."""
        self._env_file_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if self._env_file_path.exists() else ""
        content = [prefix, "# Local infrastructure defaults managed by Codex.\n"]
        content.extend(f"{key}={value}\n" for key, value in missing_values.items())
        with self._env_file_path.open("a", encoding="utf-8") as env_file:
            env_file.writelines(content)

    def _parse_line(self, line: str) -> tuple[str, str]:
        """Parse one `.env` line and ignore comments/blank/invalid lines."""
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#") or "=" not in stripped_line:
            return "", ""
        key, raw_value = stripped_line.split("=", 1)
        return key.strip(), raw_value.strip().strip("'\"")


class SchemaManifest:
    """Load and validate approved PostgreSQL schema file execution order.

    This prevents ad-hoc SQL execution order issues by enforcing one curated
    manifest (`schema_creation_order.md`) as the source of truth.
    """

    _SCHEMA_ENTRY_PATTERN = re.compile(r"^\s*\d+\.\s+`([^`]+\.sql)`")

    def __init__(self, manifest_path: Path, schema_directory: Path) -> None:
        """Initialize with manifest location and schema directory path."""
        self._manifest_path = manifest_path
        self._schema_directory = schema_directory

    def ordered_schema_files(self) -> list[Path]:
        """Return schema file paths in documented creation order.

        Raises:
            FileNotFoundError: If manifest references a missing SQL file.
        """
        schema_file_names = self._schema_file_names()
        schema_files = [self._schema_directory / name for name in schema_file_names]
        missing_files = [schema_file.name for schema_file in schema_files if not schema_file.exists()]
        if missing_files:
            raise FileNotFoundError(f"Schema manifest references missing files: {missing_files}")
        return schema_files

    def _schema_file_names(self) -> list[str]:
        """Extract ordered schema file names from manifest markdown."""
        manifest_text = self._manifest_path.read_text(encoding="utf-8")
        file_names = [
            match.group(1)
            for line in manifest_text.splitlines()
            if (match := self._SCHEMA_ENTRY_PATTERN.match(line))
        ]
        if not file_names:
            raise ValueError(f"No schema files found in manifest: {self._manifest_path}")
        return file_names


class DockerComposeClient:
    """Adapter around Docker Compose commands used by this script.

    Rationale:
        Encapsulating command construction and error translation keeps shell
        execution concerns separate from infrastructure orchestration logic.
    """

    def __init__(self, root_directory: Path, compose_file_path: Path) -> None:
        """Initialize compose execution context (cwd and compose file)."""
        self._root_directory = root_directory
        self._compose_file_path = compose_file_path

    def run(self, compose_arguments: Sequence[str], input_text: str | None = None) -> None:
        """Execute Compose command and raise `CommandFailedError` on failure."""
        command = self._compose_command(compose_arguments)
        completed_process = subprocess.run(
            command,
            cwd=self._root_directory,
            input=input_text,
            text=True,
            check=False,
        )
        if completed_process.returncode != 0:
            raise CommandFailedError(f"Command failed with exit code {completed_process.returncode}")

    def output(self, compose_arguments: Sequence[str]) -> str:
        """Execute Compose command and return trimmed stdout text."""
        completed_process = subprocess.run(
            self._compose_command(compose_arguments),
            cwd=self._root_directory,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed_process.returncode != 0:
            raise CommandFailedError(completed_process.stderr.strip())
        return completed_process.stdout.strip()

    def call(self, compose_arguments: Sequence[str]) -> int:
        """Execute interactive Compose command and return raw exit code."""
        return subprocess.call(self._compose_command(compose_arguments), cwd=self._root_directory)

    def _compose_command(self, compose_arguments: Sequence[str]) -> list[str]:
        """Build full `docker compose` command with configured compose file."""
        return ["docker", "compose", "-f", str(self._compose_file_path), *compose_arguments]


class LocalInfrastructureManager:
    """Coordinate lifecycle, health checks, and schema operations.

    Design principle:
        This class orchestrates "what to do" while `DockerComposeClient`
        handles "how commands are run".
    """

    def __init__(
        self,
        compose_client: DockerComposeClient,
        environment_file: LocalEnvironmentFile,
        schema_manifest: SchemaManifest,
    ) -> None:
        """Initialize manager with explicit injected collaborators."""
        self._compose_client = compose_client
        self._environment_file = environment_file
        self._schema_manifest = schema_manifest

    def start(self) -> None:
        """Start services, wait for readiness, bootstrap Vault, and apply schema."""
        environment = self._ensure_environment()
        self._compose_client.run(["up", "-d", *INFRASTRUCTURE_SERVICES])
        self._wait_for_services(environment)
        self._bootstrap_vault()
        self.apply_schema(environment)
        self.print_connection_details(environment)

    def stop(self) -> None:
        """Stop infrastructure containers while preserving named volumes/data."""
        self._compose_client.run(["stop", *INFRASTRUCTURE_SERVICES, "vault-init"])

    def restart(self) -> None:
        """Restart infrastructure by running `stop` followed by `start`."""
        self.stop()
        self.start()

    def status(self) -> None:
        """Print container status plus resolved local connection details."""
        environment = self._ensure_environment()
        print(self._compose_client.output(["ps", "vault", "vault-init", "postgres", "redis"]))
        self.print_connection_details(environment)

    def apply_schema(self, environment: LocalInfrastructureEnvironment | None = None) -> None:
        """Apply manifest-approved SQL files inside Postgres container.

        Args:
            environment: Optional pre-resolved environment values. If omitted,
                values are resolved from `.env`.
        """
        resolved_environment = environment or self._ensure_environment()
        self._wait_for_postgres(resolved_environment)
        for schema_file in self._schema_manifest.ordered_schema_files():
            print(f"Applying schema: {schema_file.name}")
            self._apply_schema_file(schema_file, resolved_environment)

    def connect_postgres(self) -> int:
        """Open interactive `psql` inside Postgres container.

        Returns:
            int: Exit code from interactive compose command.
        """
        environment = self._ensure_environment()
        return self._compose_client.call(
            [
                "exec",
                "postgres",
                "psql",
                "-U",
                environment.postgres_user,
                "-d",
                environment.postgres_database,
            ]
        )

    def connect_redis(self) -> int:
        """Open interactive `redis-cli` session inside Redis container.

        Returns:
            int: Exit code from interactive compose command.
        """
        environment = self._ensure_environment()
        return self._compose_client.call(
            ["exec", "redis", "redis-cli", "-a", environment.redis_password]
        )

    def reset(self, confirm_delete_volumes: bool) -> None:
        """Delete containers and volumes only with explicit confirmation flag.

        Args:
            confirm_delete_volumes: Must be `True` to allow destructive reset.

        Raises:
            ValueError: If destructive confirmation flag is absent.
        """
        if not confirm_delete_volumes:
            raise ValueError("Refusing reset without --confirm-delete-volumes.")
        self._compose_client.run(["down", "--volumes", "--remove-orphans"])

    def print_connection_details(self, environment: LocalInfrastructureEnvironment) -> None:
        """Print local connection coordinates and helper commands.

        Args:
            environment: Resolved local connection values.
        """
        print("\nLocal infrastructure connection details")
        print("---------------------------------------")
        print(f"Postgres URL: {environment.postgres_url}")
        print(f"Postgres user: {environment.postgres_user}")
        print(f"Postgres password: {environment.postgres_password}")
        print(f"Postgres database: {environment.postgres_database}")
        print(f"Redis URL: {environment.redis_url}")
        print(f"Redis password: {environment.redis_password}")
        print("Vault URL: http://localhost:8200")
        print(f"Vault root token: {environment.vault_root_token}")
        print(f"Vault service username: {environment.vault_service_username}")
        print(f"Vault service password: {environment.vault_service_password}")
        print(f"Vault KV prefix: {environment.vault_kv_prefix}")
        print("\nUseful commands")
        print("---------------")
        print("python infrastructure/manage_local_infrastructure.py connect postgres")
        print("python infrastructure/manage_local_infrastructure.py connect redis")

    def _ensure_environment(self) -> LocalInfrastructureEnvironment:
        """Resolve and validate required local environment values from `.env`."""
        values = self._environment_file.ensure_required_values()
        postgres_user = self._required_value(values, "POSTGRES_USER")
        postgres_password = self._required_value(values, "POSTGRES_PASSWORD")
        postgres_database = self._required_value(values, "POSTGRES_DB")
        return LocalInfrastructureEnvironment(
            postgres_user=postgres_user,
            postgres_password=postgres_password,
            postgres_database=postgres_database,
            redis_password=self._required_value(values, "REDIS_PASSWORD"),
            vault_root_token=self._required_value(values, "VAULT_ROOT_TOKEN"),
            vault_service_username=self._required_value(values, "VAULT_SERVICE_USERNAME"),
            vault_service_password=self._required_value(values, "VAULT_SERVICE_PASSWORD"),
            vault_kv_prefix=self._required_value(values, "VAULT_KV_PREFIX"),
        )

    def _wait_for_services(self, environment: LocalInfrastructureEnvironment) -> None:
        """Wait for Postgres, Redis, and Vault readiness in deterministic order."""
        self._wait_for_postgres(environment)
        self._wait_for_redis(environment)
        self._wait_for_vault()

    def _wait_for_postgres(self, environment: LocalInfrastructureEnvironment) -> None:
        """Poll Postgres readiness using `pg_isready` from inside container."""
        self._wait_until(
            "Postgres",
            [
                "exec",
                "-T",
                "postgres",
                "pg_isready",
                "-U",
                environment.postgres_user,
                "-d",
                environment.postgres_database,
            ],
        )

    def _wait_for_redis(self, environment: LocalInfrastructureEnvironment) -> None:
        """Poll Redis readiness by running `redis-cli ping` in container."""
        self._wait_until(
            "Redis",
            ["exec", "-T", "redis", "redis-cli", "-a", environment.redis_password, "ping"],
        )

    def _wait_for_vault(self) -> None:
        """Poll Vault readiness using `vault status` in container."""
        self._wait_until("Vault", ["exec", "-T", "vault", "vault", "status"])

    def _wait_until(self, service_name: str, command: Sequence[str]) -> None:
        """Poll command until success or timeout.

        Args:
            service_name: Friendly service label used in progress/error output.
            command: Compose command arguments to test service readiness.

        Raises:
            TimeoutError: If service is still not ready after 90 seconds.
        """
        deadline_seconds = time.monotonic() + 90
        while time.monotonic() < deadline_seconds:
            if self._command_succeeds(command):
                print(f"{service_name} is ready.")
                return
            time.sleep(2)
        raise TimeoutError(f"{service_name} did not become ready within 90 seconds.")

    def _command_succeeds(self, command: Sequence[str]) -> bool:
        """Return whether compose command succeeds without raising error."""
        try:
            self._compose_client.output(command)
        except CommandFailedError:
            return False
        return True

    def _bootstrap_vault(self) -> None:
        """Run one-shot Vault bootstrap container to initialize local secrets setup."""
        self._compose_client.run(["up", "--force-recreate", "vault-init"])

    def _apply_schema_file(
        self,
        schema_file: Path,
        environment: LocalInfrastructureEnvironment,
    ) -> None:
        """Execute one schema file via `psql` with fail-fast SQL behavior.

        The `ON_ERROR_STOP=1` flag ensures SQL failures abort immediately,
        preventing partial schema application from being silently accepted.
        """
        self._compose_client.run(
            [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                environment.postgres_user,
                "-d",
                environment.postgres_database,
            ],
            input_text=schema_file.read_text(encoding="utf-8"),
        )

    def _required_value(self, values: Mapping[str, str], key: str) -> str:
        """Return required env value or raise explicit error when missing."""
        value = values.get(key)
        if not value:
            raise ValueError(f"Required local infrastructure value is missing: {key}")
        return value


def build_argument_parser() -> argparse.ArgumentParser:
    """Build CLI parser and subcommands for infrastructure lifecycle tasks.

    Returns:
        argparse.ArgumentParser: Parser with lifecycle, schema, reset, and
            connect subcommands.
    """
    parser = argparse.ArgumentParser(description="Manage local Postgres, Redis, and Vault.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("start", help="Start local infrastructure and apply schema.")
    subparsers.add_parser("stop", help="Stop local infrastructure without deleting volumes.")
    subparsers.add_parser("restart", help="Restart local infrastructure and apply schema.")
    subparsers.add_parser("status", help="Show container status and connection details.")
    reset_parser = subparsers.add_parser("reset", help="Delete containers and named volumes.")
    reset_parser.add_argument("--confirm-delete-volumes", action="store_true")
    connect_parser = subparsers.add_parser("connect", help="Open an interactive service shell.")
    connect_parser.add_argument("service", choices=("postgres", "redis"))
    schema_parser = subparsers.add_parser("schema", help="Manage PostgreSQL schema.")
    schema_parser.add_argument("action", choices=("apply",))
    return parser


def build_manager() -> LocalInfrastructureManager:
    """Create manager with production collaborators bound to repository paths."""
    return LocalInfrastructureManager(
        compose_client=DockerComposeClient(ROOT_DIRECTORY, COMPOSE_FILE_PATH),
        environment_file=LocalEnvironmentFile(ENV_FILE_PATH),
        schema_manifest=SchemaManifest(SCHEMA_ORDER_PATH, SCHEMA_DIRECTORY),
    )


def run_command(manager: LocalInfrastructureManager, arguments: argparse.Namespace) -> int:
    """Dispatch parsed CLI arguments to manager methods.

    Args:
        manager: Infrastructure manager handling orchestration logic.
        arguments: Parsed CLI namespace.

    Returns:
        int: Process exit code. `0` for success by default, or command-specific
            code for interactive connect commands.
    """
    if arguments.command == "start":
        manager.start()
    elif arguments.command == "stop":
        manager.stop()
    elif arguments.command == "restart":
        manager.restart()
    elif arguments.command == "status":
        manager.status()
    elif arguments.command == "schema":
        manager.apply_schema()
    elif arguments.command == "connect" and arguments.service == "postgres":
        return manager.connect_postgres()
    elif arguments.command == "connect" and arguments.service == "redis":
        return manager.connect_redis()
    elif arguments.command == "reset":
        manager.reset(confirm_delete_volumes=arguments.confirm_delete_volumes)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run CLI entrypoint and normalize operational errors to exit code `1`.

    Args:
        argv: Optional command arguments. If omitted, uses process argv.

    Returns:
        int: Process exit code.
    """
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    try:
        return run_command(build_manager(), arguments)
    except (CommandFailedError, FileNotFoundError, TimeoutError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
