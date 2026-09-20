"""Read-only host prevalidation for local infrastructure commands."""

from __future__ import annotations

import os
import platform
import socket
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from infrastructure.local_stack import console
from infrastructure.local_stack.constants import MINIMUM_PYTHON_VERSION, SERVICE_PORTS
from infrastructure.local_stack.errors import CommandFailedError, PreflightFailedError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from infrastructure.local_stack.docker_compose import DockerComposeClient
    from infrastructure.local_stack.environment import LocalEnvironmentFile
    from infrastructure.local_stack.schema import SchemaManifest


class CheckStatus(StrEnum):
    """Severity of one host prevalidation result."""

    PASS = "pass"
    WARNING = "warning"
    FAILURE = "failure"


@dataclass(frozen=True)
class CheckResult:
    """One actionable preflight result."""

    name: str
    status: CheckStatus
    detail: str
    remediation: str | None = None


@dataclass(frozen=True)
class PreflightReport:
    """Complete ordered preflight result set."""

    results: tuple[CheckResult, ...]

    @property
    def ready(self) -> bool:
        """Return whether no blocking failures were found."""
        return all(result.status is not CheckStatus.FAILURE for result in self.results)

    def print(self) -> None:
        """Render a concise, actionable report consistently on every OS."""
        console.heading("Local infrastructure preflight")
        for result in self.results:
            message = f"{result.name}: {result.detail}"
            if result.status is CheckStatus.PASS:
                console.success(message)
            elif result.status is CheckStatus.WARNING:
                console.warning(message)
            else:
                console.failure(message)
            if result.remediation:
                print(f"       Fix: {result.remediation}")
        print()
        if self.ready:
            console.success("Host is ready for local infrastructure commands.")
        else:
            console.failure("Resolve failed checks before starting infrastructure.")

    def require_ready(self) -> None:
        """Raise when mutation would be unsafe on the current host."""
        if not self.ready:
            failed_names = [
                result.name for result in self.results if result.status is CheckStatus.FAILURE
            ]
            raise PreflightFailedError(f"Preflight failed: {', '.join(failed_names)}")


class InfrastructurePreflight:
    """Check host, repository, configuration, Docker, schema, and ports read-only."""

    def __init__(
        self,
        compose: DockerComposeClient,
        environment_file: LocalEnvironmentFile,
        schema_manifest: SchemaManifest,
        required_paths: Sequence[Path],
    ) -> None:
        """Receive explicit collaborators so every check is independently testable."""
        self.compose = compose
        self.environment_file = environment_file
        self.schema_manifest = schema_manifest
        self.required_paths = tuple(required_paths)

    def run(self) -> PreflightReport:
        """Run every safe check and return all findings instead of failing fast."""
        results = [
            self.check_platform(),
            self.check_python(),
            self.check_project_files(),
            self.check_environment_file(),
            self.check_schema_manifest(),
            self.check_docker_cli(),
        ]
        docker_available = results[-1].status is CheckStatus.PASS
        if docker_available:
            daemon = self.check_docker_daemon()
            compose = self.check_compose_plugin()
            results.extend([daemon, compose])
            if daemon.status is CheckStatus.PASS and compose.status is CheckStatus.PASS:
                results.append(self.check_compose_configuration())
                results.extend(self.check_service_ports())
        return PreflightReport(tuple(results))

    def check_platform(self) -> CheckResult:
        """Accept Windows, Linux, and macOS while reporting the detected host."""
        system = platform.system()
        if system in {"Windows", "Linux", "Darwin"}:
            return CheckResult("Operating system", CheckStatus.PASS, system)
        return CheckResult(
            "Operating system",
            CheckStatus.FAILURE,
            system or "unknown",
            "Use Windows, Linux, or macOS with Docker Desktop/Engine.",
        )

    def check_python(self) -> CheckResult:
        """Verify the repository's minimum Python runtime without invoking a shell."""
        detected = sys.version_info[:3]
        detail = ".".join(str(part) for part in detected)
        if detected >= MINIMUM_PYTHON_VERSION:
            return CheckResult("Python", CheckStatus.PASS, detail)
        required = ".".join(str(part) for part in MINIMUM_PYTHON_VERSION)
        return CheckResult(
            "Python",
            CheckStatus.FAILURE,
            f"{detail}; requires {required}+",
            f"Install Python {required} or newer and rerun this command.",
        )

    def check_project_files(self) -> CheckResult:
        """Ensure required repository inputs exist before Docker is invoked."""
        missing = [str(path) for path in self.required_paths if not path.is_file()]
        if not missing:
            return CheckResult("Project files", CheckStatus.PASS, "all required files exist")
        return CheckResult(
            "Project files",
            CheckStatus.FAILURE,
            f"missing {len(missing)} file(s)",
            "Restore: " + ", ".join(missing),
        )

    def check_environment_file(self) -> CheckResult:
        """Validate existing `.env` syntax without creating or changing it."""
        inspection = self.environment_file.inspect()
        if not self.environment_file.path.exists():
            return CheckResult(
                ".env",
                CheckStatus.WARNING,
                "not initialized",
                "Run `python -m infrastructure init`; start also initializes it.",
            )
        if not inspection.is_valid:
            problems: list[str] = []
            if inspection.duplicate_keys:
                problems.append("duplicate keys " + ", ".join(inspection.duplicate_keys))
            if inspection.malformed_lines:
                problems.append(
                    "malformed lines " + ", ".join(str(line) for line in inspection.malformed_lines)
                )
            return CheckResult(
                ".env",
                CheckStatus.FAILURE,
                "; ".join(problems),
                "Correct the listed lines; existing values are never overwritten automatically.",
            )
        if inspection.missing_keys:
            return CheckResult(
                ".env",
                CheckStatus.WARNING,
                "missing values that init/start will generate",
                "Missing: " + ", ".join(inspection.missing_keys),
            )
        if not os.access(self.environment_file.path, os.W_OK):
            return CheckResult(
                ".env",
                CheckStatus.FAILURE,
                "file is not writable",
                "Grant the current user write access to the project .env file.",
            )
        return CheckResult(".env", CheckStatus.PASS, "syntax and required values are valid")

    def check_schema_manifest(self) -> CheckResult:
        """Resolve the entire schema order before any database mutation."""
        try:
            files = self.schema_manifest.ordered_schema_files()
        except (FileNotFoundError, OSError, ValueError) as error:
            return CheckResult(
                "Schema manifest",
                CheckStatus.FAILURE,
                str(error),
                "Repair postgres_schema/schema_creation_order.md or its referenced files.",
            )
        return CheckResult(
            "Schema manifest",
            CheckStatus.PASS,
            f"{len(files)} ordered SQL files validated",
        )

    def check_docker_cli(self) -> CheckResult:
        """Verify Docker is on PATH."""
        executable = self.compose.docker_executable()
        if executable:
            return CheckResult("Docker CLI", CheckStatus.PASS, executable)
        return CheckResult(
            "Docker CLI",
            CheckStatus.FAILURE,
            "not found on PATH",
            "Install Docker Desktop (Windows/macOS) or Docker Engine (Linux).",
        )

    def check_docker_daemon(self) -> CheckResult:
        """Verify the Docker engine is running and reachable by the current user."""
        try:
            version = self.compose.docker_output(["version", "--format", "{{.Server.Version}}"])
        except (CommandFailedError, OSError) as error:
            return CheckResult(
                "Docker engine",
                CheckStatus.FAILURE,
                str(error),
                "Start Docker Desktop/Engine and verify the current user can access it.",
            )
        return CheckResult("Docker engine", CheckStatus.PASS, f"server {version}")

    def check_compose_plugin(self) -> CheckResult:
        """Require Docker Compose v2 (`docker compose`), not legacy docker-compose."""
        try:
            version = self.compose.output(["version", "--short"])
        except (CommandFailedError, OSError) as error:
            return CheckResult(
                "Docker Compose",
                CheckStatus.FAILURE,
                str(error),
                "Install/enable the Docker Compose v2 plugin.",
            )
        return CheckResult("Docker Compose", CheckStatus.PASS, version)

    def check_compose_configuration(self) -> CheckResult:
        """Ask Compose to parse and interpolate the project before startup."""
        try:
            self.compose.output(["config", "--quiet"])
        except (CommandFailedError, OSError) as error:
            return CheckResult(
                "Compose configuration",
                CheckStatus.FAILURE,
                str(error),
                "Correct docker-compose.yml or referenced environment values.",
            )
        return CheckResult("Compose configuration", CheckStatus.PASS, "valid")

    def check_service_ports(self) -> tuple[CheckResult, ...]:
        """Detect cross-platform host port conflicts without netstat/lsof parsing."""
        try:
            running_services = self.compose.running_services()
        except (CommandFailedError, OSError):
            running_services = set()
        results: list[CheckResult] = []
        for service, port in SERVICE_PORTS.items():
            occupied = self.port_is_open(port)
            if not occupied:
                results.append(CheckResult(f"Port {port}", CheckStatus.PASS, "available"))
            elif service in running_services:
                results.append(
                    CheckResult(
                        f"Port {port}",
                        CheckStatus.PASS,
                        f"owned by running Compose service {service}",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        f"Port {port}",
                        CheckStatus.WARNING,
                        "already in use by another process",
                        f"Start will reclaim this port for the {service} service.",
                    )
                )
        return tuple(results)

    @staticmethod
    def port_is_open(port: int) -> bool:
        """Return whether a TCP listener accepts connections on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(0.25)
            return client.connect_ex(("127.0.0.1", port)) == 0
