"""Cross-concern safety rules for composed environment settings."""

from __future__ import annotations

import pytest

from app.core.settings.settings import ApplicationSettings

_REQUIRED_SETTINGS = {
    "database_url": "postgresql+asyncpg://user:password@db/service",
    "encryption_master_key": "test-encryption-master-key-material",
    "jwt_secret_key": "test-jwt-secret-key-material-over-32-bytes",
}


def test_production_rejects_local_cors_origin() -> None:
    """A production API cannot accidentally retain development browser access."""
    with pytest.raises(ValueError, match="local origins"):
        ApplicationSettings(app_environment="production", **_REQUIRED_SETTINGS)


def test_production_accepts_explicit_external_origin() -> None:
    """A complete production configuration remains constructible."""
    settings = ApplicationSettings(
        app_environment="production",
        cors_allowed_origins="https://dashboard.example.com",
        **_REQUIRED_SETTINGS,
    )

    assert settings.get_cors_allowed_origins() == ["https://dashboard.example.com"]
