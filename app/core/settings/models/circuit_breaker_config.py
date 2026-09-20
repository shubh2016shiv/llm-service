"""Validated configuration for provider circuit-breaker behavior."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
