"""Validate circuit-breaker policy keys against the provider catalog.

Architecture:
    base.yaml policies + provider YAML catalog -> startup validation

Provider-specific overrides are optional, but every override must name a
provider that the process actually loaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core.settings.loader import ConfigLoader
from app.core.settings.models.circuit_breaker_config import (
    CircuitBreakerPolicyConfig,
    ProviderCircuitBreakerConfig,
)

if TYPE_CHECKING:
    from app.core.settings.settings import ApplicationSettings


def test_validate_provider_names_accepts_known_override_subset() -> None:
    """Providers without explicit overrides may continue using the default."""
    # Arrange
    config = ProviderCircuitBreakerConfig(providers={"bedrock": CircuitBreakerPolicyConfig()})

    # Act / Assert
    config.validate_provider_names({"bedrock", "openai", "vllm"})


def test_validate_provider_names_rejects_unknown_overrides_in_sorted_order() -> None:
    """Configuration drift fails startup with deterministic diagnostics."""
    # Arrange
    config = ProviderCircuitBreakerConfig(
        providers={
            "gcp_vertex": CircuitBreakerPolicyConfig(),
            "aws_bedrock": CircuitBreakerPolicyConfig(),
        }
    )

    # Act / Assert
    with pytest.raises(ValueError, match="aws_bedrock, gcp_vertex"):
        config.validate_provider_names({"bedrock", "openai"})


def test_project_breaker_overrides_match_catalog_and_apply_bedrock_policy(
    test_settings: ApplicationSettings,
) -> None:
    """The checked-in YAML catalog and policy keys remain aligned."""
    # Arrange
    loader = ConfigLoader(test_settings.config_dir, test_settings.app_environment)
    global_config = loader.load_global_config()
    provider_configs = loader.load_all_provider_configs()

    # Act
    global_config.provider_circuit_breakers.validate_provider_names(provider_configs.keys())
    bedrock_policy = global_config.provider_circuit_breakers.policy_for("bedrock")
    vllm_policy = global_config.provider_circuit_breakers.policy_for("vllm")

    # Assert
    assert bedrock_policy.failure_threshold == 3
    assert bedrock_policy.reset_timeout_seconds == 45
    assert vllm_policy == global_config.provider_circuit_breakers.default
