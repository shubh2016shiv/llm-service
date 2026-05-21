"""
Provider Circuit Breaker
========================

Redis-backed, per-provider aiobreaker instances shared across all
FastAPI asynchronous workers.

Architecture:
-------------
    ┌─────────────────────────────────┐
    │  BaseProvider.generate()        │
    │  (app/providers/base.py)        │
    └───────────────┬─────────────────┘
                    │ get_provider_circuit_breaker(name)
                    ▼
    ┌─────────────────────────────────┐
    │  provider_circuit_breaker.py             │
    │  _registry (local cache)        │
    └───────────────┬─────────────────┘
                    │ CircuitRedisStorage
                    ▼
    ┌─────────────────────────────────┐
    │  RedisCache                     │
    │  (app/infrastructure/redis_cache.py)  │
    └─────────────────────────────────┘

Dependencies:
    - aiobreaker: circuit breaker implementation
    - app/infrastructure/redis_cache.py: Redis client

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import aiobreaker
from aiobreaker.storage.memory import CircuitMemoryStorage
from aiobreaker.storage.redis import CircuitRedisStorage

if TYPE_CHECKING:
    from aiobreaker.storage.base import CircuitBreakerStorage

    from app.infrastructure.redis_cache import RedisCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ProviderBreakerConfig:
    """Immutable per-provider circuit breaker parameters."""

    failure_threshold: int
    reset_timeout_seconds: int


_PROVIDER_CONFIGS: dict[str, _ProviderBreakerConfig] = {
    # Global endpoints — recover fast; tolerate up to 5 failures
    "openai": _ProviderBreakerConfig(failure_threshold=5, reset_timeout_seconds=60),
    "anthropic": _ProviderBreakerConfig(failure_threshold=5, reset_timeout_seconds=60),
    # Regional cloud endpoints — tighter tolerance, faster failover
    "azure_openai": _ProviderBreakerConfig(failure_threshold=3, reset_timeout_seconds=30),
    "gcp_vertex": _ProviderBreakerConfig(failure_threshold=3, reset_timeout_seconds=30),
    "aws_bedrock": _ProviderBreakerConfig(failure_threshold=3, reset_timeout_seconds=45),
}

_DEFAULT_PROVIDER_CONFIG = _ProviderBreakerConfig(failure_threshold=5, reset_timeout_seconds=60)

_CB_NAMESPACE_PREFIX = "cb:provider"


class _CircuitBreakerListener(aiobreaker.CircuitBreakerListener):
    """Listens to circuit breaker state changes and logs them structurally."""

    def state_change(
        self,
        breaker: aiobreaker.CircuitBreaker,
        old: aiobreaker.CircuitBreakerState,
        new: aiobreaker.CircuitBreakerState,
    ) -> None:
        logger.warning(
            "Circuit breaker state changed",
            extra={
                "circuit_breaker_name": breaker.name,
                "old_state": old.name,
                "new_state": new.name,
            },
        )


_LISTENER = _CircuitBreakerListener()

# ---------------------------------------------------------------------------
# Registry — async-safe singleton cache
# ---------------------------------------------------------------------------

_registry: dict[str, aiobreaker.CircuitBreaker] = {}
_registry_lock = asyncio.Lock()


def _build_provider_storage(provider_name: str, cache: RedisCache) -> CircuitBreakerStorage:
    """Build a Redis-backed storage for a single provider's circuit breaker."""
    try:
        redis_client = cache._redis
        if redis_client is None:
            raise RuntimeError("Redis client is not connected.")
        return CircuitRedisStorage(
            state=aiobreaker.CircuitBreakerState.CLOSED,
            redis_object=redis_client,
            namespace=f"{_CB_NAMESPACE_PREFIX}:{provider_name}",
            fallback_circuit_state=aiobreaker.CircuitBreakerState.OPEN,
        )
    except Exception:
        logger.exception(
            "Redis storage unavailable; falling back to local OPEN storage",
            extra={"provider": provider_name},
        )
        return CircuitMemoryStorage(aiobreaker.CircuitBreakerState.OPEN)


def _create_provider_circuit_breaker(
    provider_name: str, cache: RedisCache
) -> aiobreaker.CircuitBreaker:
    """Instantiate a new circuit breaker for the given provider."""
    config = _PROVIDER_CONFIGS.get(provider_name, _DEFAULT_PROVIDER_CONFIG)
    storage = _build_provider_storage(provider_name, cache)

    breaker = aiobreaker.CircuitBreaker(
        fail_max=config.failure_threshold,
        timeout_duration=timedelta(seconds=config.reset_timeout_seconds),
        state_storage=storage,
        listeners=[_LISTENER],
        name=f"provider:{provider_name}",
    )
    logger.info(
        "Registered circuit breaker",
        extra={
            "provider": provider_name,
            "threshold": config.failure_threshold,
            "reset_seconds": config.reset_timeout_seconds,
        },
    )
    return breaker


async def get_provider_circuit_breaker(
    provider_name: str, cache: RedisCache
) -> aiobreaker.CircuitBreaker:
    """Return the circuit breaker for the named LLM provider.

    Creates and registers a new breaker on first call for a given provider;
    returns the cached instance on every subsequent call. Async-safe.

    Args:
        provider_name: Canonical provider key (e.g. "openai", "azure_openai").

    Returns:
        A thread-safe aiobreaker.CircuitBreaker instance.
    """
    if provider_name in _registry:
        return _registry[provider_name]

    async with _registry_lock:
        if provider_name in _registry:
            return _registry[provider_name]

        breaker = _create_provider_circuit_breaker(provider_name, cache)
        _registry[provider_name] = breaker
        return breaker


def get_all_provider_breaker_states() -> dict[str, str]:
    """Return current state of every registered provider circuit breaker."""
    return {
        provider_name: breaker.current_state.name.upper()
        for provider_name, breaker in _registry.items()
    }
