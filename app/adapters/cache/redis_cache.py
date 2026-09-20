"""Redis key-value cache adapter.

Architecture:
    application services -> RedisCache -> RedisConnectionManager -> Redis

Only cache semantics live here. Connection lifecycle, retries, health,
metrics, pub/sub, and raw-client access belong to neighboring adapters.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.adapters.cache.redis_connection import BACKEND_ERRORS, RedisConnectionManager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence


class RedisCache:
    """Store disposable byte values without making Redis a hard dependency."""

    def __init__(self, connection: RedisConnectionManager) -> None:
        """Use the process-wide Redis connection manager."""
        self._connection = connection

    async def get(self, key: str) -> bytes | None:
        """Return a value, or ``None`` for a miss or unavailable Redis."""
        client = await self._connection.acquire()
        if client is None:
            self._connection.count("get", "unavailable")
            return None
        try:
            value = await client.get(key)
        except BACKEND_ERRORS as error:
            self._connection.handle_error("get", error)
            logger.debug("Redis GET failed", extra={"cache_key": key}, exc_info=True)
            return None
        self._connection.count("get", "hit" if value is not None else "miss")
        return value

    async def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        """Return values in the same order as ``keys``."""
        if not keys:
            return []
        client = await self._connection.acquire()
        if client is None:
            self._connection.count("get_many", "unavailable", len(keys))
            return [None] * len(keys)
        try:
            values = await client.mget(keys)
        except BACKEND_ERRORS as error:
            self._connection.handle_error("get_many", error)
            logger.debug("Redis MGET failed", extra={"key_count": len(keys)}, exc_info=True)
            return [None] * len(keys)
        hit_count = sum(value is not None for value in values)
        self._connection.count("get_many", "hit", hit_count)
        self._connection.count("get_many", "miss", len(values) - hit_count)
        return values

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = 300) -> bool:
        """Store bytes with an optional expiry; report whether Redis accepted them."""
        client = await self._connection.acquire()
        if client is None:
            self._connection.count("set", "unavailable")
            return False
        try:
            await client.set(key, value, ex=ttl_seconds)
        except BACKEND_ERRORS as error:
            self._connection.handle_error("set", error)
            logger.debug("Redis SET failed", extra={"cache_key": key}, exc_info=True)
            return False
        self._connection.count("set", "ok")
        return True

    async def delete(self, key: str) -> bool:
        """Delete a key; report whether Redis processed the command."""
        client = await self._connection.acquire()
        if client is None:
            self._connection.count("delete", "unavailable")
            return False
        try:
            await client.delete(key)
        except BACKEND_ERRORS as error:
            self._connection.handle_error("delete", error)
            logger.debug("Redis DELETE failed", extra={"cache_key": key}, exc_info=True)
            return False
        self._connection.count("delete", "ok")
        return True
