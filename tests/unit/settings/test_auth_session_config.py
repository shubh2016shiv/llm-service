"""Startup guards for the guest door and the issued-token lifetime."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.settings.settings import ApplicationSettings

GUEST_USER_ID = "7cbb6261-c5fb-4b3d-bda4-13139bfd04cf"


def build_settings(**overrides: Any) -> ApplicationSettings:
    """Construct settings from explicit values, ignoring any ambient .env."""
    base: dict[str, Any] = {
        "encryption_master_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "jwt_secret_key": "k" * 40,
        "database_url": "postgresql+asyncpg://user:pass@host:5432/db",
    }
    base.update(overrides)
    return ApplicationSettings(**base)


def production_overrides(**extra: Any) -> dict[str, Any]:
    """Values that satisfy every *other* production guard, so one rule is tested at a time."""
    overrides: dict[str, Any] = {
        "app_environment": "production",
        "jwt_secret_key": "z" * 40,
        "encryption_master_key": "Bm9wcXJzdHV2d3h5ejAxMjM0NTY3ODlhYmNkZWY=",
        "cors_allowed_origins": "https://console.example.com",
    }
    overrides.update(extra)
    return overrides


def test_settings_when_guest_unset_defaults_to_disabled() -> None:
    # The unauthenticated door must require a deliberate act to open.
    assert build_settings().guest_superuser_enabled is False


def test_settings_when_guest_enabled_without_user_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="guest_superuser_user_id is required"):
        build_settings(guest_superuser_enabled=True)


def test_settings_when_guest_enabled_with_user_id_in_development_is_accepted() -> None:
    settings = build_settings(
        guest_superuser_enabled=True,
        guest_superuser_user_id=GUEST_USER_ID,
    )

    assert settings.guest_superuser_enabled is True
    assert settings.guest_superuser_role == "owner"


def test_settings_when_production_and_guest_enabled_raises_value_error() -> None:
    # The guard that matters most: a misconfigured production deploy would
    # otherwise publish an unauthenticated superuser.
    with pytest.raises(ValueError, match="unauthenticated superuser access"):
        build_settings(
            **production_overrides(
                guest_superuser_enabled=True,
                guest_superuser_user_id=GUEST_USER_ID,
            )
        )


def test_settings_when_production_and_guest_disabled_is_accepted() -> None:
    settings = build_settings(**production_overrides())

    assert settings.app_environment == "production"
    assert settings.guest_superuser_enabled is False


def test_settings_when_token_ttl_exceeds_validator_ceiling_raises_value_error() -> None:
    with pytest.raises(ValueError, match="would issue tokens its own validator rejects"):
        build_settings(access_token_ttl_seconds=7200, jwt_max_token_age_seconds=3600)


def test_settings_when_token_ttl_equals_validator_ceiling_is_accepted() -> None:
    settings = build_settings(access_token_ttl_seconds=3600, jwt_max_token_age_seconds=3600)

    assert settings.access_token_ttl_seconds == 3600
