"""Specification tests for environment-backed Vault configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.settings.models.vault_config import VaultConfig


@pytest.mark.parametrize("invalid_fraction", [0.0, -0.1, 1.0, 1.1])
def test_vault_config_with_refresh_fraction_outside_lease_rejects_value(
    invalid_fraction: float,
) -> None:
    """REQ: token refresh must occur strictly after lease start and before expiry."""
    with pytest.raises(ValidationError):
        VaultConfig(vault_token_refresh_after_lease_fraction=invalid_fraction)


def test_vault_config_without_refresh_override_uses_safe_default() -> None:
    """REQ: Vault tokens refresh after 90 percent of the lease by default."""
    config = VaultConfig()

    assert config.vault_token_refresh_after_lease_fraction == 0.9


def test_vault_config_rejects_inverted_retry_delay_bounds() -> None:
    """The initial retry cap cannot be larger than the configured maximum."""
    with pytest.raises(ValidationError, match="cannot exceed"):
        VaultConfig(
            vault_retry_base_delay_seconds=3.0,
            vault_retry_max_delay_seconds=2.0,
        )
