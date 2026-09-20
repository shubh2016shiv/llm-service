"""Behavioral tests for the per-provider circuit-breaker registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.adapters.provider_transport import ProviderCircuitBreakerRegistry
from app.core.settings.models.circuit_breaker_config import (
    CircuitBreakerPolicyConfig,
    ProviderCircuitBreakerConfig,
)

if TYPE_CHECKING:
    from datetime import timedelta


@pytest.mark.asyncio
async def test_get_breaker_returns_same_instance_for_same_provider() -> None:
    """Repeated lookups preserve one process-local breaker per provider."""
    registry = build_registry()

    first = await registry.get_breaker("openai")
    second = await registry.get_breaker("openai")

    assert second is first
    assert registry.snapshot_states() == {"openai": "CLOSED"}


@pytest.mark.asyncio
async def test_get_breaker_canonicalizes_provider_name() -> None:
    """Cosmetic name differences cannot create duplicate breakers."""
    registry = build_registry()

    first = await registry.get_breaker(" OpenAI ")
    second = await registry.get_breaker("openai")

    assert second is first
    assert registry.snapshot_states() == {"openai": "CLOSED"}


@pytest.mark.asyncio
async def test_get_breaker_rejects_unsafe_provider_name() -> None:
    """Invalid names fail before becoming policy keys or telemetry fields."""
    registry = build_registry()

    with pytest.raises(ValueError, match="provider_name must contain"):
        await registry.get_breaker("openai:production")


@pytest.mark.asyncio
async def test_get_breaker_uses_provider_specific_config() -> None:
    """A named override replaces the default operational policy."""
    registry = build_registry(
        ProviderCircuitBreakerConfig(
            providers={
                "openai": CircuitBreakerPolicyConfig(
                    failure_threshold=2,
                    reset_timeout_seconds=15,
                )
            }
        )
    )

    breaker = await registry.get_breaker("openai")

    assert breaker.fail_max == 2
    assert cast("timedelta", breaker.timeout_duration).total_seconds() == 15


@pytest.mark.asyncio
async def test_different_providers_receive_independent_breakers() -> None:
    """One unhealthy upstream must not consume another provider's fuse."""
    registry = build_registry()

    openai = await registry.get_breaker("openai")
    anthropic = await registry.get_breaker("anthropic")

    assert anthropic is not openai
    assert registry.snapshot_states() == {
        "openai": "CLOSED",
        "anthropic": "CLOSED",
    }


def build_registry(
    config: ProviderCircuitBreakerConfig | None = None,
) -> ProviderCircuitBreakerRegistry:
    """Create a registry with defaults unless a test supplies an override."""
    return ProviderCircuitBreakerRegistry(config or ProviderCircuitBreakerConfig())
