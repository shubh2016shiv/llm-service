"""Tests for aggregate, read-only infrastructure prevalidation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from infrastructure.local_stack.environment import LocalEnvironmentFile
from infrastructure.local_stack.preflight import CheckStatus, InfrastructurePreflight
from infrastructure.local_stack.schema import SchemaManifest

if TYPE_CHECKING:
    from infrastructure.local_stack.docker_compose import DockerComposeClient


class HealthyDockerCompose:
    """Return deterministic successful Docker and Compose diagnostics."""

    @staticmethod
    def docker_executable() -> str:
        return "/usr/bin/docker"

    def docker_output(self, arguments) -> str:
        return "27.0.0"

    def output(self, arguments) -> str:
        return "2.30.0" if "version" in arguments else ""

    def running_services(self) -> set[str]:
        return set()


class MissingDockerCompose(HealthyDockerCompose):
    """Simulate a machine where Docker is not installed."""

    @staticmethod
    def docker_executable() -> None:
        return None


def test_run_with_valid_project_and_missing_env_returns_ready_with_warning(
    tmp_path,
    monkeypatch,
) -> None:
    """A missing `.env` is repairable by start and therefore does not block readiness."""
    preflight = build_preflight(tmp_path, HealthyDockerCompose())
    monkeypatch.setattr(
        InfrastructurePreflight,
        "port_is_open",
        staticmethod(lambda port: False),
    )

    report = preflight.run()

    env_result = next(result for result in report.results if result.name == ".env")
    assert report.ready
    assert env_result.status is CheckStatus.WARNING


def test_run_without_docker_reports_failure_and_skips_dependent_checks(tmp_path) -> None:
    """A missing Docker CLI produces one actionable root failure without cascaded noise."""
    preflight = build_preflight(tmp_path, MissingDockerCompose())

    report = preflight.run()

    docker_result = next(result for result in report.results if result.name == "Docker CLI")
    assert not report.ready
    assert docker_result.status is CheckStatus.FAILURE
    assert all(result.name != "Docker engine" for result in report.results)


def build_preflight(tmp_path, compose) -> InfrastructurePreflight:
    """Create valid project inputs around a selected Docker behavior fake."""
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")
    schema_directory = tmp_path / "postgres_schema"
    schema_directory.mkdir()
    schema_file = schema_directory / "one.sql"
    schema_file.write_text("SELECT 1;", encoding="utf-8")
    manifest = schema_directory / "schema_creation_order.md"
    manifest.write_text("1. `one.sql`\n", encoding="utf-8")
    return InfrastructurePreflight(
        compose=cast("DockerComposeClient", compose),
        environment_file=LocalEnvironmentFile(tmp_path / ".env"),
        schema_manifest=SchemaManifest(manifest, schema_directory),
        required_paths=(compose_file, manifest),
    )
