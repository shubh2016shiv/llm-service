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
        ApplicationSettings.model_validate({"app_environment": "production", **_REQUIRED_SETTINGS})


def test_production_accepts_explicit_external_origin() -> None:
    """A complete production configuration remains constructible."""
    settings = ApplicationSettings.model_validate(
        {
            "app_environment": "production",
            "cors_allowed_origins": "https://dashboard.example.com",
            **_REQUIRED_SETTINGS,
        }
    )

    assert settings.get_cors_allowed_origins() == ["https://dashboard.example.com"]


@pytest.mark.parametrize(
    ("field_name", "placeholder"),
    [
        ("jwt_secret_key", "local-jwt-signing-key-replace-outside-development"),
        ("encryption_master_key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
    ],
)
def test_production_rejects_compose_development_secrets(field_name: str, placeholder: str) -> None:
    """REQ: production never starts with the known compose development keys."""
    values = {**_REQUIRED_SETTINGS, field_name: placeholder}

    with pytest.raises(ValueError, match=f"production {field_name}"):
        ApplicationSettings.model_validate(
            {
                "app_environment": "production",
                "cors_allowed_origins": "https://dashboard.example.com",
                **values,
            }
        )


@pytest.mark.parametrize("provided_field", ["vault_admin_username", "vault_admin_password"])
def test_settings_reject_half_configured_vault_writer(provided_field: str) -> None:
    """REQ: a missing half of the Vault write identity fails before requests."""
    from app.core.settings.models.vault_config import VaultConfig

    with pytest.raises(ValueError, match="must both be set"):
        VaultConfig.model_validate({provided_field: "present"})
