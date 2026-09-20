"""Redis publish/subscribe adapter.

Architecture:
    event producers/consumers -> RedisPubSub -> RedisConnectionManager -> Redis

A cache answers key-value questions; pub/sub transports transient events.
They share connection policy but expose separate application capabilities.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from app.adapters.cache.redis_connection import BACKEND_ERRORS, RedisConnectionManager

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from redis.asyncio.client import PubSub

logger = logging.getLogger(__name__)


class RedisPubSub:
    """Publish events and maintain resilient Redis subscriptions."""

    def __init__(self, connection: RedisConnectionManager) -> None:
        """Use the process-wide Redis connection manager."""
        self._connection = connection

    async def publish(self, channel: str, message: str) -> bool:
        """Publish a message; zero active subscribers still means success."""
        client = await self._connection.acquire()
        if client is None:
            self._connection.count("publish", "unavailable")
            return False
        try:
            subscriber_count = await client.publish(channel, message)
        except BACKEND_ERRORS as error:
            self._connection.handle_error("publish", error)
            logger.debug("Redis PUBLISH failed", extra={"channel": channel}, exc_info=True)
            return False
        self._connection.count("publish", "ok")
        if subscriber_count == 0:
            self._connection.count("publish", "no_subscribers")
        return True

    async def subscribe(self, channel: str) -> AsyncIterator[str]:
        """Yield messages continuously, reconnecting after transient failures."""
        delay_seconds = self._connection.initial_retry_delay_seconds
        while not self._connection.is_closed:
            pubsub = await self._open_subscription(channel)
            if pubsub is not None:
                delay_seconds = self._connection.initial_retry_delay_seconds
                stream = self._read_subscription(pubsub, channel)
                try:
                    async with contextlib.aclosing(stream) as messages:
                        async for payload in messages:
                            yield payload
                finally:
                    await _release_pubsub(pubsub, channel)
            if not self._connection.is_closed:
                await asyncio.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2, self._connection.max_retry_delay_seconds)

    async def _open_subscription(self, channel: str) -> PubSub | None:
        """Create and subscribe a handle, or return ``None`` for retry."""
        client = await self._connection.acquire()
        if client is None:
            self._connection.count("subscribe", "unavailable")
            return None
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(channel)
        except BACKEND_ERRORS as error:
            self._connection.handle_error("subscribe", error)
            await _release_pubsub(pubsub, channel)
            return None
        return pubsub

    async def _read_subscription(self, pubsub: PubSub, channel: str) -> AsyncGenerator[str, None]:
        """Filter Redis control frames and yield application payloads."""
        try:
            async for raw_message in pubsub.listen():
                payload = _extract_payload(raw_message)
                if payload is not None:
                    self._connection.count("subscribe", "message")
                    yield payload
        except asyncio.CancelledError:
            raise
        except BACKEND_ERRORS as error:
            if not self._connection.is_closed:
                self._connection.handle_error("subscribe", error)
                logger.warning(
                    "Redis subscription dropped; resubscribing",
                    extra={"channel": channel},
                    exc_info=True,
                )


def _extract_payload(raw_message: object) -> str | None:
    """Return an application message and ignore Redis control frames."""
    if not isinstance(raw_message, dict) or raw_message.get("type") != "message":
        return None
    data = raw_message.get("data", "")
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


async def _release_pubsub(pubsub: PubSub, channel: str) -> None:
    """Unsubscribe and return the dedicated connection to its pool."""
    with contextlib.suppress(*BACKEND_ERRORS):
        await pubsub.unsubscribe(channel)
    with contextlib.suppress(*BACKEND_ERRORS):
        await pubsub.aclose()  # type: ignore[attr-defined]
