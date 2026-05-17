"""
Local Infrastructure Manager - controlled lifecycle for local dependencies.

Architecture:
-------------
    Developer Shell
          |
          v
    LocalInfrastructureManager
          |
          v
    Docker Compose CLI
          |
          +-- postgres - durable relational data
          +-- redis - local cache and counters
          +-- vault - ephemeral local secret backend

Dependencies:
    - docker-compose.yml - local service definitions and named volumes.
    - postgres_schema/schema_creation_order.md - schema files allowed to run.

Author: Engineering Team
Last Updated: 2026-05-17
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
    """Resolved local infrastructure connection values."""

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
        """Return the host-side async PostgreSQL URL used by the application."""
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@localhost:5432/{self.postgres_database}"
        )

    @property
    def redis_url(self) -> str:
        """Return the host-side Redis URL used by local tools."""
        return f"redis://:{self.redis_password}@localhost:6379/0"


class CommandFailedError(RuntimeError):
    """Raised when a required Docker command fails."""


class LocalEnvironmentFile:
    """Read and update the gitignored local .env file."""

    def __init__(self, env_file_path: Path) -> None:
        """Store the path to the local environment file."""
        self._env_file_path = env_file_path

    def ensure_required_values(self) -> Mapping[str, str]:
        """Append missing local infrastructure values without replacing secrets."""
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
        """Read KEY=value pairs from .env using the subset Docker Compose needs."""
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
        return {
            key: value
            for key, value in REQUIRED_ENVIRONMENT_DEFAULTS.items()
            if not current_values.get(key)
        }

    def _missing_derived_values(self, current_values: Mapping[str, str]) -> dict[str, str]:
        derived_values = self._derived_values(current_values)
        return {key: value for key, value in derived_values.items() if not current_values.get(key)}

    def _derived_values(self, current_values: Mapping[str, str]) -> Mapping[str, str]:
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
        self._env_file_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if self._env_file_path.exists() else ""
        content = [prefix, "# Local infrastructure defaults managed by Codex.\n"]
        content.extend(f"{key}={value}\n" for key, value in missing_values.items())
        with self._env_file_path.open("a", encoding="utf-8") as env_file:
            env_file.writelines(content)

    def _parse_line(self, line: str) -> tuple[str, str]:
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#") or "=" not in stripped_line:
            return "", ""
        key, raw_value = stripped_line.split("=", 1)
        return key.strip(), raw_value.strip().strip("'\"")


class SchemaManifest:
    """Load the approved PostgreSQL schema creation order."""

    _SCHEMA_ENTRY_PATTERN = re.compile(r"^\s*\d+\.\s+`([^`]+\.sql)`")

    def __init__(self, manifest_path: Path, schema_directory: Path) -> None:
        """Store manifest and schema directory paths."""
        self._manifest_path = manifest_path
        self._schema_directory = schema_directory

    def ordered_schema_files(self) -> list[Path]:
        """Return schema file paths in the exact order documented for creation."""
        schema_file_names = self._schema_file_names()
        schema_files = [self._schema_directory / name for name in schema_file_names]
        missing_files = [schema_file.name for schema_file in schema_files if not schema_file.exists()]
        if missing_files:
            raise FileNotFoundError(f"Schema manifest references missing files: {missing_files}")
        return schema_files

    def _schema_file_names(self) -> list[str]:
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
    """Small wrapper around Docker Compose commands used by local infra."""

    def __init__(self, root_directory: Path, compose_file_path: Path) -> None:
        """Store Compose execution context."""
        self._root_directory = root_directory
        self._compose_file_path = compose_file_path

    def run(self, compose_arguments: Sequence[str], input_text: str | None = None) -> None:
        """Run Docker Compose and raise with context if it fails."""
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
        """Run Docker Compose and return stdout."""
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
        """Run an interactive Docker Compose command and return its exit code."""
        return subprocess.call(self._compose_command(compose_arguments), cwd=self._root_directory)

    def _compose_command(self, compose_arguments: Sequence[str]) -> list[str]:
        return ["docker", "compose", "-f", str(self._compose_file_path), *compose_arguments]


class LocalInfrastructureManager:
    """Coordinate local service lifecycle, health checks, and schema creation."""

    def __init__(
        self,
        compose_client: DockerComposeClient,
        environment_file: LocalEnvironmentFile,
        schema_manifest: SchemaManifest,
    ) -> None:
        """Initialize the manager with explicit dependencies."""
        self._compose_client = compose_client
        self._environment_file = environment_file
        self._schema_manifest = schema_manifest

    def start(self) -> None:
        """Start local infrastructure and apply the approved PostgreSQL schema."""
        environment = self._ensure_environment()
        self._compose_client.run(["up", "-d", *INFRASTRUCTURE_SERVICES])
        self._wait_for_services(environment)
        self._bootstrap_vault()
        self.apply_schema(environment)
        self.print_connection_details(environment)

    def stop(self) -> None:
        """Stop infrastructure containers without deleting named volumes."""
        self._compose_client.run(["stop", *INFRASTRUCTURE_SERVICES, "vault-init"])

    def restart(self) -> None:
        """Restart infrastructure while preserving local data volumes."""
        self.stop()
        self.start()

    def status(self) -> None:
        """Show service status and local connection details."""
        environment = self._ensure_environment()
        print(self._compose_client.output(["ps", "vault", "vault-init", "postgres", "redis"]))
        self.print_connection_details(environment)

    def apply_schema(self, environment: LocalInfrastructureEnvironment | None = None) -> None:
        """Apply manifest-approved DDL files through psql inside the container."""
        resolved_environment = environment or self._ensure_environment()
        self._wait_for_postgres(resolved_environment)
        for schema_file in self._schema_manifest.ordered_schema_files():
            print(f"Applying schema: {schema_file.name}")
            self._apply_schema_file(schema_file, resolved_environment)

    def connect_postgres(self) -> int:
        """Open an interactive psql session inside the Postgres container."""
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
        """Open an interactive redis-cli session inside the Redis container."""
        environment = self._ensure_environment()
        return self._compose_client.call(
            ["exec", "redis", "redis-cli", "-a", environment.redis_password]
        )

    def reset(self, confirm_delete_volumes: bool) -> None:
        """Delete containers and volumes only when the explicit guard flag is present."""
        if not confirm_delete_volumes:
            raise ValueError("Refusing reset without --confirm-delete-volumes.")
        self._compose_client.run(["down", "--volumes", "--remove-orphans"])

    def print_connection_details(self, environment: LocalInfrastructureEnvironment) -> None:
        """Print local development connection details after lifecycle commands."""
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
        self._wait_for_postgres(environment)
        self._wait_for_redis(environment)
        self._wait_for_vault()

    def _wait_for_postgres(self, environment: LocalInfrastructureEnvironment) -> None:
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
        self._wait_until(
            "Redis",
            ["exec", "-T", "redis", "redis-cli", "-a", environment.redis_password, "ping"],
        )

    def _wait_for_vault(self) -> None:
        self._wait_until("Vault", ["exec", "-T", "vault", "vault", "status"])

    def _wait_until(self, service_name: str, command: Sequence[str]) -> None:
        deadline_seconds = time.monotonic() + 90
        while time.monotonic() < deadline_seconds:
            if self._command_succeeds(command):
                print(f"{service_name} is ready.")
                return
            time.sleep(2)
        raise TimeoutError(f"{service_name} did not become ready within 90 seconds.")

    def _command_succeeds(self, command: Sequence[str]) -> bool:
        try:
            self._compose_client.output(command)
        except CommandFailedError:
            return False
        return True

    def _bootstrap_vault(self) -> None:
        self._compose_client.run(["up", "--force-recreate", "vault-init"])

    def _apply_schema_file(
        self,
        schema_file: Path,
        environment: LocalInfrastructureEnvironment,
    ) -> None:
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
        value = values.get(key)
        if not value:
            raise ValueError(f"Required local infrastructure value is missing: {key}")
        return value


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for local infrastructure management."""
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
    """Create the manager with production dependencies."""
    return LocalInfrastructureManager(
        compose_client=DockerComposeClient(ROOT_DIRECTORY, COMPOSE_FILE_PATH),
        environment_file=LocalEnvironmentFile(ENV_FILE_PATH),
        schema_manifest=SchemaManifest(SCHEMA_ORDER_PATH, SCHEMA_DIRECTORY),
    )


def run_command(manager: LocalInfrastructureManager, arguments: argparse.Namespace) -> int:
    """Dispatch parsed CLI arguments to the manager."""
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
    """Run the local infrastructure CLI."""
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    try:
        return run_command(build_manager(), arguments)
    except (CommandFailedError, FileNotFoundError, TimeoutError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

