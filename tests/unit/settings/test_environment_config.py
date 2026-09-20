"""Specification tests for environment and dashboard-origin configuration."""

from __future__ import annotations

import pytest

from app.core.settings.models.environment_config import EnvironmentConfig


def test_cors_origins_are_trimmed_and_empty_values_are_removed() -> None:
    """REQ: browser origins become a clean allowlist for CORS middleware."""
    config = EnvironmentConfig(
        cors_allowed_origins="http://localhost:3000, https://dashboard.example.com, ",
    )

    assert config.get_cors_allowed_origins() == [
        "http://localhost:3000",
        "https://dashboard.example.com",
    ]


def test_cors_origins_default_to_local_dashboard() -> None:
    """REQ: local UI development works without extra configuration."""
    config = EnvironmentConfig()

    assert config.get_cors_allowed_origins() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


@pytest.mark.parametrize(
    "origins",
    ["*", "dashboard.example.com", "https://user:secret@example.com", "https://example.com/api"],
)
def test_cors_origins_reject_unsafe_or_malformed_values(origins: str) -> None:
    """Authenticated APIs accept exact browser origins, never wildcard-like values."""
    with pytest.raises(ValueError):
        EnvironmentConfig(cors_allowed_origins=origins)
