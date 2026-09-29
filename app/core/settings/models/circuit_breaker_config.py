"""Validated configuration for provider circuit-breaker behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Collection


class CircuitBreakerPolicyConfig(BaseModel):
    """Define when a provider circuit opens and when recovery is attempted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_threshold: int = Field(
        default=5,
        ge=1,
        description="Consecutive failures that open the provider circuit.",
    )
    reset_timeout_seconds: int = Field(
        default=60,
        ge=1,
        description="Seconds before an open circuit permits a recovery attempt.",
    )


class ProviderCircuitBreakerConfig(BaseModel):
    """Hold the default breaker policy and provider-specific overrides."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: CircuitBreakerPolicyConfig = Field(default_factory=CircuitBreakerPolicyConfig)
    providers: dict[str, CircuitBreakerPolicyConfig] = Field(default_factory=dict)

    def policy_for(self, provider_name: str) -> CircuitBreakerPolicyConfig:
        """Return a provider override when present, otherwise the default policy."""
        return self.providers.get(provider_name, self.default)

    def validate_provider_names(self, provider_names: Collection[str]) -> None:
        """Reject overrides that cannot match the loaded provider catalog."""
        unknown_provider_names = sorted(set(self.providers).difference(provider_names))
        if unknown_provider_names:
            formatted_names = ", ".join(unknown_provider_names)
            raise ValueError(
                f"Circuit-breaker overrides reference unknown providers: {formatted_names}"
            )
