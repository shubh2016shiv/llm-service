"""Named fakes for RedisCache adapter tests.

These stand in for ``redis.asyncio.Redis`` and its ``PubSub`` handle so tests
can script backend failures -- unreachable at startup, dropped mid-operation,
recovered later -- which is exactly the behavior a live Redis cannot be asked
for on demand.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import redis

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence

# One scripted pub/sub frame, or an exception to raise instead of yielding it.
PubSubScriptItem = object


class FakePubSub:
    """Replay a scripted sequence of pub/sub frames, then end the stream."""

    def __init__(
        self,
        script: Sequence[PubSubScriptItem],
        *,
        drop_event: asyncio.Event | None = None,
    ) -> None:
        """Store the frames (or exceptions) this handle will produce.

        Args:
            script: Frames to replay before the stream ends or parks.
            drop_event: When set, a listener parked in ``listen()`` after the
                script is exhausted raises a ConnectionError, modeling a pool
                closed underneath an active subscription.
        """
        self.script = list(script)
        self.drop_event = drop_event
        self.awaiting_drop = asyncio.Event()
        self.subscribed_channels: list[str] = []
        self.unsubscribed_channels: list[str] = []
        self.aclose_count = 0

    async def subscribe(self, channel: str) -> None:
        """Record the subscription request."""
        self.subscribed_channels.append(channel)

    async def listen(self) -> AsyncIterator[object]:
        """Yield each scripted frame, raising any scripted exception in order."""
        for item in self.script:
            if isinstance(item, BaseException):
                raise item
            yield item
        if self.drop_event is not None:
            self.awaiting_drop.set()
            await self.drop_event.wait()
            raise redis.ConnectionError("fake connection pool closed")

    async def unsubscribe(self, channel: str) -> None:
        """Record the unsubscribe request."""
        self.unsubscribed_channels.append(channel)

    async def aclose(self) -> None:
        """Record that the pooled pub/sub connection was released."""
        self.aclose_count += 1


class FakeAsyncRedis:
    """In-memory async Redis client with scriptable connection failures."""

    def __init__(
        self,
        *,
        ping_errors: int = 0,
        command_error: Exception | None = None,
        subscriber_count: int = 0,
        pubsub_scripts: Iterable[Sequence[PubSubScriptItem]] | None = None,
        pubsub_drop_event: asyncio.Event | None = None,
    ) -> None:
        """Configure how many pings fail and how commands behave afterwards.

        Args:
            ping_errors: Number of leading ``ping()`` calls that fail, which
                models a Redis that is down and later comes back.
            command_error: Exception raised by every data command. Connection
                errors model an outage; a ``TypeError`` models a caller defect.
            subscriber_count: Value returned by ``publish``.
            pubsub_scripts: One frame script per ``pubsub()`` call.
            pubsub_drop_event: Optional event handed to every pub/sub handle so
                ``aclose()`` can wake a listener parked inside ``listen()``.
        """
        self.values: dict[str, bytes] = {}
        self.remaining_ping_errors = ping_errors
        self.command_error = command_error
        self.subscriber_count = subscriber_count
        self.pubsub_scripts = [list(script) for script in (pubsub_scripts or [])]
        self.pubsub_drop_event = pubsub_drop_event
        self.published_messages: list[tuple[str, str]] = []
        self.pubsub_handles: list[FakePubSub] = []
        self.ping_count = 0
        self.aclose_count = 0

    async def ping(self) -> bool:
        """Fail while scripted ping errors remain, then report healthy."""
        self.ping_count += 1
        if self.remaining_ping_errors > 0:
            self.remaining_ping_errors -= 1
            raise redis.ConnectionError("fake redis is unreachable")
        return True

    async def get(self, key: str) -> bytes | None:
        """Return the stored value for one key."""
        self._raise_scripted_command_error()
        return self.values.get(key)

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        """Return stored values for several keys, preserving argument order."""
        self._raise_scripted_command_error()
        return [self.values.get(key) for key in keys]

    async def set(self, key: str, value: bytes, ex: int | None = None) -> bool:
        """Store one value, ignoring expiry because tests never wait for it."""
        self._raise_scripted_command_error()
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        """Remove one key and report how many keys were removed."""
        self._raise_scripted_command_error()
        return int(self.values.pop(key, None) is not None)

    async def eval(
        self,
        _script: str,
        key_count: int,
        *keys_and_arguments: object,
    ) -> int:
        """Emulate the conditional-write Lua contract used by RedisCache."""
        self._raise_scripted_command_error()
        keys = [str(value) for value in keys_and_arguments[:key_count]]
        arguments = keys_and_arguments[key_count:]
        comparison_count = key_count - 1
        for index, comparison_key in enumerate(keys[1:]):
            presence_flag = arguments[index * 2]
            expected_value = arguments[index * 2 + 1]
            current_value = self.values.get(comparison_key)
            if presence_flag == "0" and current_value is not None:
                return 0
            if presence_flag == "1" and current_value != expected_value:
                return 0
        payload = arguments[comparison_count * 2]
        if not isinstance(payload, bytes):
            raise TypeError("conditional-write payload must be bytes")
        self.values[keys[0]] = payload
        return 1

    async def publish(self, channel: str, message: str) -> int:
        """Record a published message and report the subscriber count."""
        self._raise_scripted_command_error()
        self.published_messages.append((channel, message))
        return self.subscriber_count

    def pubsub(self) -> FakePubSub:
        """Return the next scripted pub/sub handle, or an empty one."""
        script = self.pubsub_scripts.pop(0) if self.pubsub_scripts else []
        handle = FakePubSub(script, drop_event=self.pubsub_drop_event)
        self.pubsub_handles.append(handle)
        return handle

    async def aclose(self) -> None:
        """Record the pool close and wake any listener parked on it."""
        self.aclose_count += 1
        for handle in self.pubsub_handles:
            if handle.drop_event is not None:
                handle.drop_event.set()

    def _raise_scripted_command_error(self) -> None:
        """Raise the scripted command failure, if one was configured."""
        if self.command_error is not None:
            raise self.command_error


class RecordingClientFactory:
    """Stand in for ``redis.asyncio.from_url`` and hand out one fake client.

    Returning the same client across reconnects lets a test script an outage
    and a recovery on a single object.
    """

    def __init__(self, client: FakeAsyncRedis) -> None:
        """Bind the client every connection attempt will receive."""
        self.client = client
        self.call_count = 0
        self.received_urls: list[str] = []
        self.received_kwargs: list[dict[str, object]] = []

    def __call__(self, url: str, **kwargs: object) -> FakeAsyncRedis:
        """Record the connection attempt and return the shared fake client."""
        self.call_count += 1
        self.received_urls.append(url)
        self.received_kwargs.append(kwargs)
        return self.client


def build_message_frame(payload: bytes) -> dict[str, object]:
    """Build a pub/sub data frame carrying one payload."""
    return {"type": "message", "channel": b"config:changes", "data": payload}


def build_subscribe_confirmation_frame() -> dict[str, object]:
    """Build the control frame Redis sends when a subscription is accepted."""
    return {"type": "subscribe", "channel": b"config:changes", "data": 1}
