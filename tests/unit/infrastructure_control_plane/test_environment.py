"""Tests for safe local `.env` inspection and initialization."""

from __future__ import annotations

from uuid import UUID

from infrastructure.local_stack.environment import LocalEnvironmentFile


def test_initialize_preserves_existing_values_and_encodes_derived_urls(tmp_path) -> None:
    """Existing credentials remain authoritative and URL-sensitive characters are encoded."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            (
                "POSTGRES_USER=local-user",
                "POSTGRES_PASSWORD=p@ss:word",
                "POSTGRES_DB=local-db",
                "REDIS_PASSWORD=redis/password",
                "VAULT_ROOT_TOKEN=root-token",
                "VAULT_SERVICE_USERNAME=vault-user",
                "VAULT_SERVICE_PASSWORD=vault-password",
            )
        ),
        encoding="utf-8",
    )
    environment_file = LocalEnvironmentFile(env_path)

    environment = environment_file.initialize()

    values = environment_file.inspect().values
    assert environment.postgres_password == "p@ss:word"
    assert values["DATABASE_URL"] == (
        "postgresql+asyncpg://local-user:p%40ss%3Aword@localhost:5432/local-db"
    )
    assert values["REDIS_URL"] == "redis://:redis%2Fpassword@localhost:6379/0"


def test_inspect_reports_duplicate_keys_and_malformed_lines_without_writing(tmp_path) -> None:
    """Ambiguous configuration is reported and left unchanged for manual correction."""
    env_path = tmp_path / ".env"
    original = "POSTGRES_USER=first\nthis is invalid\nPOSTGRES_USER=second\n"
    env_path.write_text(original, encoding="utf-8")

    inspection = LocalEnvironmentFile(env_path).inspect()

    assert inspection.duplicate_keys == ("POSTGRES_USER",)
    assert inspection.malformed_lines == (2,)
    assert env_path.read_text(encoding="utf-8") == original


def test_initialize_missing_file_generates_complete_unique_local_configuration(tmp_path) -> None:
    """A first run produces every required primary and application-facing value."""
    environment_file = LocalEnvironmentFile(tmp_path / ".env")

    environment_file.initialize()

    inspection = environment_file.inspect()
    assert inspection.is_valid
    assert inspection.missing_keys == ()
    assert inspection.values["DATABASE_URL"]
    assert inspection.values["REDIS_URL"]
    assert inspection.values["VAULT_TOKEN_REFRESH_AFTER_LEASE_FRACTION"] == "0.9"
    assert len(inspection.values["JWT_SECRET_KEY"]) == 64
    assert inspection.values["JWT_ALGORITHM"] == "HS256"
    assert inspection.values["LOCAL_DASHBOARD_JWT_ROLE"] == "owner"
    assert UUID(inspection.values["LOCAL_DASHBOARD_JWT_USER_ID"])
