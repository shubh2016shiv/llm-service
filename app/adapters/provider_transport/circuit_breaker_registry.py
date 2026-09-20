"""
Per-provider circuit breakers — the outbound fuse box
=====================================================

Why this adapter exists
-----------------------
An unhealthy AI provider can fail slowly. Without a circuit breaker, every
request waits for the same timeout and consumes a connection while doing so.
A breaker stops those calls after a configured failure threshold and permits a
probe after a cooling-off period.

The three states, in plain language
-----------------------------------
``CLOSED``
    Calls flow normally and failures are counted.
``OPEN``
    Calls fail immediately, preserving worker capacity while the provider is
    unhealthy.
``HALF_OPEN``
    The cooling-off period elapsed, so a probe is allowed to test recovery.

Why state is process-local
--------------------------
``aiobreaker`` exposes an asynchronous call API, but its Redis storage performs
blocking Redis commands whenever it reads or changes state. Using that storage
inside an asyncio web service would let a slow Redis socket freeze the whole
event loop.

Each service replica therefore owns its circuit state in memory. This is the
normal isolation boundary for a client-side circuit breaker: every replica
protects its own connection pool, Redis cannot become a dependency of provider
calls, and one noisy replica cannot open the circuit for all healthy replicas.
Central monitoring can still combine the state snapshots exposed here.

Author: Shubham Singh
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import aiobreaker

if TYPE_CHECKING:
    from app.core.settings.models.circuit_breaker_config import (
        ProviderCircuitBreakerConfig,
    )

logger = logging.getLogger(__name__)

_VALID_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class CircuitBreakerStateChangeLogger(
    aiobreaker.CircuitBreakerListener  # type: ignore[misc]
):
    """Log every fuse transition for dashboards and operational alerts."""

    def state_change(
        self,
        breaker: aiobreaker.CircuitBreaker,
        old: aiobreaker.CircuitBreakerState,
        new: aiobreaker.CircuitBreakerState,
    ) -> None:
        """Record which provider moved between which two circuit states."""
        logger.warning(
            "Provider circuit breaker state changed",
            extra={
                "circuit_breaker_name": breaker.name,
                "old_state": old.name,
                "new_state": new.name,
            },
        )


class ProviderCircuitBreakerRegistry:
    """Create and retain exactly one in-memory breaker per provider.

    The first request for a provider creates its breaker. Later requests take
    the lock-free dictionary fast path. A lock around creation ensures two
    concurrent first requests cannot create two breakers with separate failure
    counters.

    The registry owns breaker *state*, while provider classes own breaker
    *usage*. Keeping those responsibilities separate makes policy setup easy to
    test without mixing it into HTTP request code.
    """

    def __init__(self, config: ProviderCircuitBreakerConfig) -> None:
        """Store validated policy; breaker objects are created lazily."""
        self._config = config
        self._breakers: dict[str, aiobreaker.CircuitBreaker] = {}
        self._creation_lock = asyncio.Lock()
        self._state_change_logger = CircuitBreakerStateChangeLogger()

    async def get_breaker(self, provider_name: str) -> aiobreaker.CircuitBreaker:
        """Return one canonical provider's breaker, creating it when absent.

        Names are normalized before lookup. Consequently ``" OpenAI "`` and
        ``"openai"`` cannot receive independent failure counters by accident.
        """
        normalized_name = self._normalize_provider_name(provider_name)

        # Fast path: almost every request finds an existing breaker and never
        # touches the lock.
        existing_breaker = self._breakers.get(normalized_name)
        if existing_breaker is not None:
            return existing_breaker

        # Slow path: serialize only first-time construction, then check again
        # because another coroutine may have populated the dictionary while we
        # waited.
        async with self._creation_lock:
            existing_breaker = self._breakers.get(normalized_name)
            if existing_breaker is not None:
                return existing_breaker

            breaker = self._create_breaker(normalized_name)
            self._breakers[normalized_name] = breaker
            return breaker

    def snapshot_states(self) -> dict[str, str]:
        """Return a detached provider-to-state mapping for health and metrics.

        Example: ``{"openai": "CLOSED", "anthropic": "OPEN"}``.
        Reading in-memory state is deliberately cheap and never performs I/O.
        """
        return {
            provider_name: breaker.current_state.name.upper()
            for provider_name, breaker in self._breakers.items()
        }

    def _create_breaker(self, provider_name: str) -> aiobreaker.CircuitBreaker:
        """Build one CLOSED breaker using an override or the default policy."""
        policy = self._config.policy_for(provider_name)
        breaker = aiobreaker.CircuitBreaker(
            fail_max=policy.failure_threshold,
            timeout_duration=timedelta(seconds=policy.reset_timeout_seconds),
            listeners=[self._state_change_logger],
            name=f"provider:{provider_name}",
        )
        logger.info(
            "Provider circuit breaker registered",
            extra={
                "provider": provider_name,
                "failure_threshold": policy.failure_threshold,
                "reset_timeout_seconds": policy.reset_timeout_seconds,
                "state_storage": "memory",
            },
        )
        return breaker

    @staticmethod
    def _normalize_provider_name(provider_name: str) -> str:
        """Return a safe canonical name for lookup, policy, metrics, and logs."""
        normalized_name = provider_name.strip().lower()
        if not _VALID_PROVIDER_NAME.fullmatch(normalized_name):
            raise ValueError(
                "provider_name must contain only lowercase letters, numbers, dots, "
                "underscores, or hyphens, and must start with a letter or number"
            )
        return normalized_name
