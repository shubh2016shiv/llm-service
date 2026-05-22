"""
Redis Cache Adapter
===================

Async Redis wrapper for key/value caching and pub/sub notifications.

Why this module exists:
    Infrastructure and authorization paths need a simple, resilient cache API.
    Wrapping Redis access behind this class keeps callers independent of Redis
    client details and allows graceful behavior when Redis is unavailable.

Step-by-step usage flow:
    1. Startup calls ``connect()`` once.
    2. Callers use ``get``/``set``/``delete`` for cache operations.
    3. Config or invalidation events use ``publish``/``subscribe``.
    4. Shutdown calls ``disconnect()``.

Degradation model:
    If Redis is down, operations return safe fallback values instead of raising
    hard failures. The service keeps running, but with reduced performance.

Author: Shubham Singh
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
    """Redis adapter with async key/value and pub/sub helpers.

    What this abstracts:
        - Connection lifecycle management.
        - Safe cache operations with predictable fallback behavior.
        - Pub/sub message loops for distributed invalidation.

    Example:
        >>> cache = RedisCache("redis://localhost:6379/0")
        >>> await cache.connect()
        >>> await cache.set("demo:key", b"value", ttl_seconds=60)
        True
        >>> await cache.get("demo:key")
        b'value'
    """

    CONFIG_CHANGES_CHANNEL = "config:changes"

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        """Store Redis connection URL and initialize disconnected state."""
        self._redis_url = redis_url
        self._redis: object | None = None
        self._connected = False
        logger.info("RedisCache configured: url=%s", redis_url)

    async def connect(self) -> None:
        """Establish Redis connection pool if available.

        Safe to call repeatedly; additional calls become no-ops.

        Rationale:
            Idempotent connect simplifies startup flows and avoids accidental
            duplicate initialization when components call connect defensively.
        """
        if self._connected:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("redis package is not installed. Cache is disabled.")
            return

        try:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30,
            )
            await self._redis.ping()  # type: ignore[union-attr]
            self._connected = True
            logger.info("RedisCache connected: %s", self._redis_url)
        except Exception:
            logger.warning(
                "Redis unavailable at %s. Cache is disabled; service continues without it.",
                self._redis_url,
                exc_info=True,
            )
            self._redis = None
            self._connected = False

    async def disconnect(self) -> None:
        """Close Redis connection pool and mark adapter disconnected.

        Always safe to call during shutdown hooks.
        """
        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[union-attr]
            self._redis = None
        self._connected = False
        logger.info("RedisCache disconnected.")

    async def get(self, key: str) -> bytes | None:
        """Fetch raw bytes by key.

        Returns ``None`` when key is missing or Redis is unavailable.

        Why ``None`` on backend errors:
            Callers can treat cache failures as cache misses and continue with
            source-of-truth reads instead of failing requests.
        """
        if not self._connected or self._redis is None:
            return None
        try:
            return await self._redis.get(key)  # type: ignore[union-attr]
        except Exception:
            logger.debug("Redis GET failed for key=%s", key, exc_info=True)
            return None

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = 300) -> bool:
        """Set bytes under a key with optional TTL.

        Returns ``True`` on success and ``False`` when Redis is unavailable or
        the write fails.

        Rationale:
            Write failures should degrade gracefully because cache is an
            optimization layer, not a hard dependency for correctness.
        """
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.set(key, value, ex=ttl_seconds)  # type: ignore[union-attr]
            return True
        except Exception:
            logger.debug("Redis SET failed for key=%s", key, exc_info=True)
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from Redis.

        Returns ``True`` on success and ``False`` when unavailable/failing.

        Deletion failures are intentionally non-fatal to keep request paths
        resilient during temporary Redis issues.
        """
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.delete(key)  # type: ignore[union-attr]
            return True
        except Exception:
            logger.debug("Redis DELETE failed for key=%s", key, exc_info=True)
            return False

    async def publish(self, channel: str, message: str) -> bool:
        """Publish message on a Redis channel.

        Common use: broadcasting configuration invalidation events across
        service replicas.

        Returns boolean so callers can log or meter missed broadcasts without
        turning cache-layer turbulence into user-facing errors.
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
        """Subscribe to a channel and yield message payloads as UTF-8 strings.

        Example:
            >>> async for message in cache.subscribe("config:changes"):
            ...     print(message)

        If Redis is unavailable, this yields nothing and exits cleanly.
        """
        if not self._connected or self._redis is None:
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

    async def health_check(self) -> bool:
        """Return ``True`` only when Redis is connected and responsive.

        Intended for liveness/readiness diagnostics.
        """
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.ping()  # type: ignore[union-attr]
            return True
        except Exception:
            return False
