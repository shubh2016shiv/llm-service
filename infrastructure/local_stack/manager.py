"""Lifecycle orchestration for the local PostgreSQL, Redis, Vault, and API stack.

Architecture:
    cli -> LocalInfrastructureManager -> DockerComposeClient  -> docker compose
                                      |-> InfrastructurePreflight
                                      |-> LocalEnvironmentFile -> .env
                                      |-> SchemaManifest       -> postgres_schema/
                                      |-> InfrastructurePortReclaimer
                                      `-> TokenManagerStack    -> ../llm_token_manager

What `start` does, in order
    1. Run the read-only preflight and refuse to continue on any failure.
    2. Reclaim the fixed host ports, asking before anything is stopped.
    3. Create only the missing `.env` values.
    4. Start PostgreSQL, Redis, and Vault, and wait for each to answer.
    5. Run the one-shot Vault bootstrap (policies, auth, unseal).
    6. Apply the ordered SQL schema with fail-fast semantics.
    7. Seed the dashboard JWT's identity so the printed token works at once.
    8. Build and start the API, and wait for its health endpoint.
    9. Start the sibling token manager, without which inference fails.
   10. Print the connection table and a fresh dashboard token.

The ordering is not incidental: each step depends on the previous one being
observably complete, which is why every wait is an explicit readiness probe
rather than a sleep.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.request import urlopen

from infrastructure.local_stack import console
from infrastructure.local_stack.constants import (
    ALL_COMPOSE_SERVICES,
    APPLICATION_SERVICE,
    MANAGED_SERVICES,
)
from infrastructure.local_stack.errors import CommandFailedError
from infrastructure.local_stack.jwt_token import generate_dashboard_jwt
from infrastructure.local_stack.token_manager import TokenManagerStack

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrastructure.local_stack.docker_compose import DockerComposeClient
    from infrastructure.local_stack.environment import (
        LocalEnvironmentFile,
        LocalInfrastructureEnvironment,
    )
    from infrastructure.local_stack.port_takeover import InfrastructurePortReclaimer
    from infrastructure.local_stack.preflight import InfrastructurePreflight, PreflightReport
    from infrastructure.local_stack.schema import SchemaManifest


class LocalInfrastructureManager:
    """Coordinate safe lifecycle, readiness, schema, and interactive operations."""

    def __init__(
        self,
        compose: DockerComposeClient,
        environment_file: LocalEnvironmentFile,
        schema_manifest: SchemaManifest,
        preflight: InfrastructurePreflight,
        port_reclaimer: InfrastructurePortReclaimer,
        token_manager: TokenManagerStack | None = None,
        readiness_timeout_seconds: int = 90,
    ) -> None:
        """Receive platform-neutral collaborators and readiness policy."""
        self.compose = compose
        self.environment_file = environment_file
        self.schema_manifest = schema_manifest
        self.preflight = preflight
        self.port_reclaimer = port_reclaimer
        self.token_manager = token_manager or TokenManagerStack()
        self.readiness_timeout_seconds = readiness_timeout_seconds

    def check(self) -> PreflightReport:
        """Run and print read-only host prevalidation."""
        report = self.preflight.run()
        report.print()
        return report

    def initialize(self, show_secrets: bool = False) -> None:
        """Create only missing local `.env` values without starting containers."""
        environment = self.environment_file.initialize()
        console.success(f"Local environment initialized at {self.environment_file.path}")
        self.print_connection_details(environment, show_secrets=show_secrets)

    def start(self, show_secrets: bool = False) -> None:
        """Run the complete validated startup workflow described in this module.

        No container or file mutation occurs until the read-only preflight report
        contains no failures.

        The sibling token manager is always part of a start: inference is
        unusable without it, and there is no supported local configuration in
        which skipping it is correct. If it cannot be started, that is reported
        with manual instructions rather than aborting a stack that is otherwise
        up and usable for management work.

        Args:
            show_secrets: Print real credentials in the connection table.
        """
        report = self.check()
        report.require_ready()
        self.port_reclaimer.reclaim_managed_ports()
        environment = self.environment_file.initialize()
        self.compose.output(["config", "--quiet"])

        console.heading("Starting local infrastructure")
        self.compose.run(["up", "--detach", *MANAGED_SERVICES])
        self.wait_for_all_services(environment)
        self.bootstrap_vault()
        self.apply_schema(environment)
        self.seed_dashboard_identity(environment)
        self.compose.run(["up", "--detach", "--build", APPLICATION_SERVICE])
        self.wait_for_application()
        self.start_token_manager()
        self.print_connection_details(environment, show_secrets=show_secrets)

    def start_token_manager(self) -> None:
        """Start the sibling quota service, reporting rather than raising on failure.

        Treated as advisory on purpose: llm_services is fully usable for
        management, schema, and dashboard work without it, so a missing or
        broken sibling repository prints the manual steps instead of failing a
        start that otherwise succeeded.
        """
        console.heading("Starting llm_token_manager")
        if not self.token_manager.is_available:
            console.warning(f"llm_token_manager was not found at {self.token_manager.root}")
            self.token_manager.print_manual_steps()
            return
        try:
            changed = self.token_manager.synchronize_shared_values(
                self.environment_file.inspect().values
            )
            if changed:
                console.success(
                    "Synchronized shared values into llm_token_manager/.env: "
                    + ", ".join(changed)
                )
            else:
                console.success("llm_token_manager/.env already matches this stack.")
            self.token_manager.start()
        except (OSError, CommandFailedError, ValueError) as error:
            console.failure(f"Could not start llm_token_manager: {error}")
            self.token_manager.print_manual_steps()
            return
        if self.token_manager.is_healthy():
            console.success("llm_token_manager is ready; inference calls can reserve capacity.")
        else:
            console.warning("llm_token_manager started but is not answering its health endpoint.")
            self.token_manager.print_manual_steps()

    def stop(self) -> None:
        """Stop managed containers while preserving named volumes and data."""
        self.compose.run(["stop", *ALL_COMPOSE_SERVICES])
        console.success("Local infrastructure stopped; named volumes were preserved.")

    def restart(self, show_secrets: bool = False) -> None:
        """Stop containers safely and execute the complete validated start workflow."""
        self.stop()
        self.start(show_secrets=show_secrets)

    def status(self, show_secrets: bool = False) -> None:
        """Show Compose state without creating or modifying `.env`."""
        print(self.compose.output(["ps", *ALL_COMPOSE_SERVICES]))
        inspection = self.environment_file.inspect()
        if inspection.is_valid and not inspection.missing_keys:
            from infrastructure.local_stack.environment import LocalInfrastructureEnvironment

            environment = LocalInfrastructureEnvironment.from_values(inspection.values)
            self.print_connection_details(environment, show_secrets=show_secrets)
        else:
            console.warning("Connection details unavailable until `.env` is initialized.")

    def connection_info(self, show_secrets: bool = True) -> None:
        """Print connection credentials for the currently running local stack."""
        running_services = self.compose.running_services()
        missing_services = [
            service
            for service in (*MANAGED_SERVICES, APPLICATION_SERVICE)
            if service not in running_services
        ]
        if missing_services:
            raise CommandFailedError(
                "Connection information requested while services are not running: "
                + ", ".join(missing_services)
                + ". Run `python -m infrastructure start`."
            )
        environment = self.environment_file.require_initialized()
        self.print_connection_details(environment, show_secrets=show_secrets)

    def verify(self) -> None:
        """Check each running service using its native in-container health command."""
        environment = self.environment_file.require_initialized()
        console.heading("Running service verification")
        checks = (
            (
                "PostgreSQL",
                [
                    "exec",
                    "--no-TTY",
                    "postgres",
                    "pg_isready",
                    "--username",
                    environment.postgres_user,
                    "--dbname",
                    environment.postgres_database,
                ],
            ),
            (
                "Redis",
                [
                    "exec",
                    "--no-TTY",
                    "redis",
                    "redis-cli",
                    "--no-auth-warning",
                    "--pass",
                    environment.redis_password,
                    "ping",
                ],
            ),
            (
                "Vault",
                ["exec", "--no-TTY", "vault", "vault", "status"],
            ),
        )
        failures: list[str] = []
        for service_name, command in checks:
            try:
                self.compose.output(command)
            except CommandFailedError as error:
                console.failure(f"{service_name}: {error}")
                failures.append(service_name)
            else:
                console.success(f"{service_name} is healthy")
        # Reported but never counted as a failure: it belongs to another
        # repository, so `verify` describes its state instead of owning it.
        if self.token_manager.is_healthy():
            console.success("llm_token_manager is healthy")
        else:
            console.warning("llm_token_manager is not answering; inference calls will fail")
            self.token_manager.print_manual_steps()
        if failures:
            raise CommandFailedError("Service verification failed: " + ", ".join(failures))

    def apply_schema(
        self,
        environment: LocalInfrastructureEnvironment | None = None,
    ) -> None:
        """Apply every prevalidated SQL file with PostgreSQL fail-fast semantics."""
        resolved = environment or self.environment_file.require_initialized()
        self.wait_for_postgres(resolved)
        console.heading("Applying PostgreSQL schema")
        for schema_file in self.schema_manifest.ordered_schema_files():
            print(f"Applying {schema_file.name}")
            self.compose.run(
                [
                    "exec",
                    "--no-TTY",
                    "postgres",
                    "psql",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--username",
                    resolved.postgres_user,
                    "--dbname",
                    resolved.postgres_database,
                ],
                input_text=schema_file.read_text(encoding="utf-8"),
            )
        console.success("PostgreSQL schema is current.")

    def seed_dashboard_identity(self, environment: LocalInfrastructureEnvironment) -> None:
        """Ensure the dashboard JWT's user_id exists as a real platform user row.

        `DASHBOARD_JWT_TOKEN` is minted independently from the backend (see
        `generate_dashboard_jwt`) and only carries a `role` claim, so it is only
        useful for role-only checks out of the box. Membership and deployment
        writes additionally verify the caller's user_id exists in `users`
        (see `TenantAccessService`), which fails without a matching row. Seeding
        it here means the printed token works end-to-end immediately, with no
        manual "create a user, then reconnect with a matching JWT" detour.

        The two `.env`-sourced values are passed as psql variables and quoted
        by the server, so neither can alter the statement's structure. Both are
        already validated upstream (a UUID, and a role checked against
        DASHBOARD_JWT_ROLES) — this is the second layer, not the only one.
        """
        # The email must pass the same validation the API applies when it reads
        # users back (pydantic EmailStr, which rejects reserved TLDs such as
        # `.test` and `.local`). This row is inserted with raw SQL and so
        # bypasses that check; a `@local.test` address here made GET /users
        # fail with a 500 and the dashboard show "Could not load the control
        # plane". example.com is reserved for documentation and is accepted.
        seed_sql = """
            INSERT INTO users (
                user_id, username, email, first_name, last_name,
                password_hash, platform_role, status
            )
            VALUES (
                :'dashboard_user_id', 'dashboard-owner',
                'dashboard-owner@example.com', 'Dashboard', 'Owner',
                'not-used-for-login', :'dashboard_role', 'active'
            )
            ON CONFLICT (user_id) DO NOTHING;
        """
        self.compose.run(
            [
                "exec",
                "--no-TTY",
                "postgres",
                "psql",
                "--set",
                "ON_ERROR_STOP=1",
                "--set",
                f"dashboard_user_id={environment.dashboard_jwt_user_id}",
                "--set",
                f"dashboard_role={environment.dashboard_jwt_role}",
                "--username",
                environment.postgres_user,
                "--dbname",
                environment.postgres_database,
            ],
            input_text=seed_sql,
        )
        console.success("Dashboard JWT identity is seeded in PostgreSQL.")

    def connect(self, service: str) -> int:
        """Open an interactive PostgreSQL or Redis shell inside its container."""
        environment = self.environment_file.require_initialized()
        if service == "postgres":
            return self.compose.interactive(
                [
                    "exec",
                    "postgres",
                    "psql",
                    "--username",
                    environment.postgres_user,
                    "--dbname",
                    environment.postgres_database,
                ]
            )
        if service == "redis":
            return self.compose.interactive(
                [
                    "exec",
                    "redis",
                    "redis-cli",
                    "--no-auth-warning",
                    "--pass",
                    environment.redis_password,
                ]
            )
        raise ValueError(f"Unsupported interactive service: {service}")

    def reset(self, confirm_delete_volumes: bool) -> None:
        """Delete project containers and volumes only with explicit confirmation."""
        if not confirm_delete_volumes:
            raise ValueError("Refusing reset without --confirm-delete-volumes.")
        self.compose.run(["down", "--volumes", "--remove-orphans"])
        console.warning("Containers and named volumes were deleted; data is not recoverable.")

    def wait_for_all_services(self, environment: LocalInfrastructureEnvironment) -> None:
        """Wait for PostgreSQL, Redis, and Vault in deterministic dependency order."""
        self.wait_for_postgres(environment)
        self.wait_until(
            "Redis",
            [
                "exec",
                "--no-TTY",
                "redis",
                "redis-cli",
                "--no-auth-warning",
                "--pass",
                environment.redis_password,
                "ping",
            ],
        )
        # Deliberately not `vault status`, which exits non-zero while Vault is
        # sealed. Since Vault moved off dev mode it starts sealed on every boot,
        # and unsealing is precisely what the vault-init step that runs *after*
        # this gate does — so waiting for an unsealed Vault here would deadlock.
        # This probe asks only "is the API answering", treating sealed and
        # uninitialized as reachable.
        self.wait_until(
            "Vault",
            [
                "exec",
                "--no-TTY",
                "vault",
                "wget",
                "--spider",
                "-q",
                "http://127.0.0.1:8200/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200",
            ],
        )

    def wait_for_postgres(self, environment: LocalInfrastructureEnvironment) -> None:
        """Wait until PostgreSQL accepts connections for the configured database."""
        self.wait_until(
            "PostgreSQL",
            [
                "exec",
                "--no-TTY",
                "postgres",
                "pg_isready",
                "--username",
                environment.postgres_user,
                "--dbname",
                environment.postgres_database,
            ],
        )

    def wait_for_application(self) -> None:
        """Wait until the FastAPI container serves its host health endpoint."""
        deadline = time.monotonic() + self.readiness_timeout_seconds
        while time.monotonic() < deadline:
            try:
                with urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if 200 <= response.status < 300:
                        console.success("FastAPI is ready")
                        return
            except OSError:
                pass
            # Outside the except block so that a reachable-but-unhealthy
            # response also paces the loop instead of spinning.
            time.sleep(2)
        raise TimeoutError(
            "FastAPI did not become ready within "
            f"{self.readiness_timeout_seconds} seconds. "
            "Run `python -m infrastructure status` and inspect `docker compose logs app`."
        )

    def wait_until(self, service_name: str, command: Sequence[str]) -> None:
        """Poll a native health command until success or the configured timeout."""
        deadline = time.monotonic() + self.readiness_timeout_seconds
        while time.monotonic() < deadline:
            try:
                self.compose.output(command)
            except CommandFailedError:
                time.sleep(2)
            else:
                console.success(f"{service_name} is ready")
                return
        raise TimeoutError(
            f"{service_name} did not become ready within "
            f"{self.readiness_timeout_seconds} seconds. Run `python -m infrastructure status`."
        )

    def bootstrap_vault(self) -> None:
        """Run the idempotent one-shot Vault bootstrap service."""
        self.compose.run(["up", "--force-recreate", "vault-init"])
        console.success("Vault policies, auth, and local secrets were initialized.")

    @staticmethod
    def print_connection_details(
        environment: LocalInfrastructureEnvironment,
        show_secrets: bool,
    ) -> None:
        """Print a copy-friendly connection table with optional credential masking."""
        reveal = (lambda value: value) if show_secrets else mask_secret
        console.heading("Local connection details")
        console.table(
            ("Service", "Host", "Port", "Database / Username", "Password / Token"),
            (
                (
                    "PostgreSQL",
                    "localhost",
                    "5432",
                    f"{environment.postgres_database} / {environment.postgres_user}",
                    reveal(environment.postgres_password),
                ),
                (
                    "Redis",
                    "localhost",
                    "6379",
                    "database 0",
                    reveal(environment.redis_password),
                ),
                (
                    "Vault service",
                    "localhost",
                    "8200",
                    environment.vault_service_username,
                    reveal(environment.vault_service_password),
                ),
                (
                    "Vault root",
                    "localhost",
                    "8200",
                    "root token",
                    reveal(environment.vault_root_token),
                ),
            ),
        )
        print("\nReady-to-copy connection values")
        if show_secrets:
            print(f"DATABASE_URL={environment.postgres_url}")
            print(f"REDIS_URL={environment.redis_url}")
        else:
            print("DATABASE_URL=(hidden)")
            print("REDIS_URL=(hidden)")
        print("VAULT_ADDR=http://localhost:8200")
        print("FASTAPI_URL=http://localhost:8000")
        print("FASTAPI_HEALTH_URL=http://localhost:8000/health")
        print("FASTAPI_DOCS_URL=http://localhost:8000/docs")
        print(f"DASHBOARD_JWT_USER_ID={environment.dashboard_jwt_user_id}")
        if show_secrets:
            print(f"DASHBOARD_JWT_TOKEN={generate_dashboard_jwt(environment)}")
        else:
            print("DASHBOARD_JWT_TOKEN=(hidden)")
        if not show_secrets:
            print("Credentials are masked because --hide-secrets was requested.")


def mask_secret(value: str) -> str:
    """Mask a credential while leaving enough characters to identify accidental drift."""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}{'*' * 8}{value[-3:]}"
