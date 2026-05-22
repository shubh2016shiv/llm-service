"""
Provider Circuit Breaker Registry
=================================

Redis-backed circuit breakers scoped per provider.

Why this module exists:
    Circuit breakers protect the system from repeatedly calling an unhealthy
    upstream provider. A per-provider breaker prevents one failing provider
    from degrading traffic for all others.

Step-by-step runtime flow:
    1. Provider call path requests breaker via ``get_provider_circuit_breaker``.
    2. Registry returns cached breaker or creates one on first use.
    3. Breaker state is stored in Redis when available.
    4. On Redis issues, module falls back to local in-memory OPEN state.
    5. Listener logs state transitions for observability.

Jargon explained:
    - Circuit breaker OPEN: calls are blocked immediately.
    - Circuit breaker CLOSED: calls are allowed normally.
    - Reset timeout: cooling period before trying calls again.

Author: Shubham Singh
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
    """Immutable breaker thresholds for one provider profile.

    Rationale:
        Provider behavior differs. Regional endpoints may require stricter
        thresholds than globally distributed endpoints.
    """

    failure_threshold: int
    reset_timeout_seconds: int


_PROVIDER_CONFIGS: dict[str, _ProviderBreakerConfig] = {
    "openai": _ProviderBreakerConfig(failure_threshold=5, reset_timeout_seconds=60),
    "anthropic": _ProviderBreakerConfig(failure_threshold=5, reset_timeout_seconds=60),
    "azure_openai": _ProviderBreakerConfig(failure_threshold=3, reset_timeout_seconds=30),
    "gcp_vertex": _ProviderBreakerConfig(failure_threshold=3, reset_timeout_seconds=30),
    "aws_bedrock": _ProviderBreakerConfig(failure_threshold=3, reset_timeout_seconds=45),
}

_DEFAULT_PROVIDER_CONFIG = _ProviderBreakerConfig(failure_threshold=5, reset_timeout_seconds=60)
_CB_NAMESPACE_PREFIX = "cb:provider"


class _CircuitBreakerListener(aiobreaker.CircuitBreakerListener):
    """Log breaker state transitions for diagnosis and alerting.

    Why it matters:
        OPEN/HALF_OPEN/CLOSED changes are key operational signals during
        provider incidents and help explain sudden traffic fail-fast behavior.
    """

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
_registry: dict[str, aiobreaker.CircuitBreaker] = {}
_registry_lock = asyncio.Lock()


def _build_provider_storage(provider_name: str, cache: RedisCache) -> CircuitBreakerStorage:
    """Build storage backend for a provider breaker.

    Redis storage is preferred so multiple worker processes share breaker state.
    If Redis is unavailable, an in-memory OPEN breaker is returned as a safety
    fallback that fails fast rather than silently allowing risky traffic.
    """
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
    """Create configured breaker for one provider namespace.

    A dedicated namespace prevents state collisions across providers.
    """
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
    """Return a singleton breaker for one provider key.

    The registry uses double-checked locking to avoid duplicate breaker
    creation during concurrent first-time access.

    Args:
        provider_name: Canonical provider identifier such as ``openai``.
        cache: Redis cache wrapper used for distributed breaker state.

    Rationale:
        Registry caching avoids repeatedly constructing breaker objects and
        keeps provider-level state consistent within a worker process.
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
    """Return snapshot of all registered provider breaker states.

    Useful for health endpoints, dashboards, or admin diagnostics.
    """
    return {
        provider_name: breaker.current_state.name.upper()
        for provider_name, breaker in _registry.items()
    }
