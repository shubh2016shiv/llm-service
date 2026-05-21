"""
app/infrastructure/redis_cache.py — Async Redis cache wrapper.

Architecture
------------
    ┌──────────────────────────────┐
    │         RedisCache           │
    │                              │
    │  + get(key) → bytes | None   │
    │  + set(key, value, ttl)      │
    │  + delete(key)               │
    │  + publish(channel, msg)     │
    │  + subscribe(channel) → ...  │
    │  + health_check() → bool     │
    └──────────────────────────────┘

Design (per implementation_plan.md §10)
---------------------------------------
- Async via ``redis.asyncio`` (redis-py ≥ 5.0).
- Config change propagation: ProviderRegistry subscribes to ``config:changes``
  to invalidate cached provider instances.
- Graceful fallback: if Redis is unreachable, cache operations are no-ops and
  health_check returns False. The service remains operational (just slower).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class RedisCache:
    """Async Redis cache wrapper with pub/sub support.

    Used for:
    - Caching serialized DeploymentConfig / TenantConfig (reduces DB load).
    - Publishing config-change events → ProviderRegistry invalidates singletons.
    - General-purpose key/value caching with TTL.

    Graceful degradation: if Redis is unreachable at startup, all methods
    become safe no-ops. Call ``health_check()`` to verify connectivity.
    """

    # Channel for config-change events.
    CONFIG_CHANGES_CHANNEL = "config:changes"

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._redis_url = redis_url
        self._redis: object | None = None  # redis.asyncio.Redis
        self._connected = False
        logger.info("RedisCache configured: url=%s", redis_url)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the Redis connection pool.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._connected:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("redis is not installed. Cache is disabled.")
            return

        try:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=False,  # Raw bytes for serialized Pydantic models
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30,
            )
            await self._redis.ping()  # type: ignore[union-attr]
            self._connected = True
            logger.info("RedisCache connected: %s", self._redis_url)
        except Exception:
            logger.warning(
                "RedisCache unavailable at %s. Cache is disabled; service will "
                "operate without caching.",
                self._redis_url,
                exc_info=True,
            )
            self._redis = None
            self._connected = False

    async def disconnect(self) -> None:
        """Close the Redis connection pool gracefully."""
        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[union-attr]
            self._redis = None
        self._connected = False
        logger.info("RedisCache disconnected.")

    # ------------------------------------------------------------------
    # Key / Value
    # ------------------------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        """Retrieve a raw value by key. Returns None on miss or if Redis is down."""
        if not self._connected or self._redis is None:
            return None
        try:
            return await self._redis.get(key)  # type: ignore[union-attr]
        except Exception:
            logger.debug("Redis GET failed for key=%s", key, exc_info=True)
            return None

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = 300) -> bool:
        """Set a key with an optional TTL. Returns True on success, False if Redis is down."""
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.set(key, value, ex=ttl_seconds)  # type: ignore[union-attr]
            return True
        except Exception:
            logger.debug("Redis SET failed for key=%s", key, exc_info=True)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True on success, False if Redis is down."""
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.delete(key)  # type: ignore[union-attr]
            return True
        except Exception:
            logger.debug("Redis DELETE failed for key=%s", key, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Pub / Sub
    # ------------------------------------------------------------------

    async def publish(self, channel: str, message: str) -> bool:
        """Publish a message to a channel.

        Used to broadcast config-change events so all service replicas
        invalidate their provider caches.
        """
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.publish(channel, message)  # type: ignore[union-attr]
            logger.debug("Redis PUBLISH channel=%s", channel)
            return True
        except Exception:
            logger.debug("Redis PUBLISH failed for channel=%s", channel, exc_info=True)
            return False

    async def subscribe(self, channel: str) -> AsyncIterator[str]:
        """Subscribe to a Redis channel and yield messages as they arrive.

        Usage::

            async for message in cache.subscribe("config:changes"):
                registry.invalidate(...)
        """
        if not self._connected or self._redis is None:
            # Yield nothing if Redis is down — caller's loop exits immediately.
            return

        pubsub = None
        try:
            pubsub = self._redis.pubsub()  # type: ignore[union-attr]
            await pubsub.subscribe(channel)
            logger.info("RedisCache subscribed to channel=%s", channel)
            async for msg in pubsub.listen():
                if msg is None:
                    continue
                if msg.get("type") == "message":
                    data = msg.get("data", "")
                    if isinstance(data, bytes):
                        yield data.decode("utf-8")
                    else:
                        yield str(data)
        except asyncio.CancelledError:
            logger.debug("RedisCache subscription cancelled for channel=%s", channel)
            raise
        except Exception:
            logger.warning("RedisCache subscription error for channel=%s", channel, exc_info=True)
        finally:
            if pubsub is not None:
                with contextlib.suppress(Exception):
                    await pubsub.unsubscribe(channel)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if Redis is connected and responsive."""
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.ping()  # type: ignore[union-attr]
            return True
        except Exception:
            return False
