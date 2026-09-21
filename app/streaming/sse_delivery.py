"""Deliver provider-neutral events as a bounded, heartbeat-enabled SSE stream.

Architecture:
    AsyncIterator[StreamEventPayload]
        -> SSEStreamDelivery
        -> AsyncIterator[str]
        -> framework StreamingResponse

Only one source read may be pending per connection. There is no application
queue, so a slow network consumer stops new source reads and creates natural
backpressure instead of unbounded memory growth.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .sse_encoder import encode_sse_json, encode_sse_message
from .sse_message import SSEMessage
from .stream_event import StreamEventEnvelope, StreamEventPayload

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from pydantic import JsonValue

StreamErrorMapper = Callable[[Exception], StreamEventPayload]


@runtime_checkable
class AsyncClosable(Protocol):
    """Small capability required to close a source iterator on disconnect."""

    async def aclose(self) -> None:
        """Release resources held by the asynchronous source."""


class _ReadOutcome(Enum):
    HEARTBEAT = auto()
    END = auto()


@dataclass(slots=True)
class _DeliveryState:
    sequence: int = 0
    terminal_status: str = "completed"
    cancelled: bool = False
    pending_read: asyncio.Future[StreamEventPayload] | None = None


class SSEStreamDelivery:
    """Add delivery metadata, heartbeats, safe errors, and terminal events."""

    def __init__(
        self,
        *,
        heartbeat_interval_seconds: float,
        error_mapper: StreamErrorMapper | None = None,
    ) -> None:
        """Configure heartbeat cadence and optional application error mapping."""
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._error_mapper = error_mapper or _default_error_event

    async def stream(
        self,
        events: AsyncIterator[StreamEventPayload],
        *,
        thread_id: UUID,
        request_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield an SSE stream in which every data event names its thread."""
        iterator = events.__aiter__()
        state = _DeliveryState()
        try:
            async for message in self._stream_source(iterator, state, thread_id, request_id):
                yield message
        except asyncio.CancelledError:
            state.cancelled = True
            raise
        except Exception as exc:
            state.terminal_status = "failed"
            yield self._encode_error(exc, state, thread_id, request_id)
        finally:
            await self._close_source(iterator, state, thread_id)
        if not state.cancelled:
            yield self._encode_complete(state, thread_id, request_id)

    async def _stream_source(
        self,
        iterator: AsyncIterator[StreamEventPayload],
        state: _DeliveryState,
        thread_id: UUID,
        request_id: str | None,
    ) -> AsyncIterator[str]:
        """Read at most one event ahead while heartbeats cover quiet periods."""
        state.pending_read = asyncio.ensure_future(anext(iterator))
        while True:
            result = await self._read_or_heartbeat(state.pending_read)
            if result is _ReadOutcome.HEARTBEAT:
                yield encode_sse_message(SSEMessage(comment="heartbeat"))
                continue
            if result is _ReadOutcome.END:
                break
            yield self._encode_event(result, state, thread_id, request_id)
            state.pending_read = asyncio.ensure_future(anext(iterator))

    async def _read_or_heartbeat(
        self,
        pending_read: asyncio.Future[StreamEventPayload],
    ) -> StreamEventPayload | _ReadOutcome:
        """Wait without cancelling the source read when a heartbeat is due."""
        try:
            return await asyncio.wait_for(
                asyncio.shield(pending_read),
                timeout=self._heartbeat_interval_seconds,
            )
        except TimeoutError:
            return _ReadOutcome.HEARTBEAT
        except StopAsyncIteration:
            return _ReadOutcome.END

    def _encode_event(
        self,
        event: StreamEventPayload,
        state: _DeliveryState,
        thread_id: UUID,
        request_id: str | None,
    ) -> str:
        """Attach stable delivery metadata to one producer event."""
        envelope = self._next_envelope(event.data, state, thread_id, request_id)
        return encode_sse_json(
            envelope.model_dump(mode="json"),
            event_name=event.event_name,
        )

    def _encode_error(
        self,
        exc: Exception,
        state: _DeliveryState,
        thread_id: UUID,
        request_id: str | None,
    ) -> str:
        """Convert a post-header failure into a safe, thread-scoped event."""
        event = self._map_error_safely(exc, thread_id, request_id)
        return self._encode_event(event, state, thread_id, request_id)

    def _encode_complete(
        self,
        state: _DeliveryState,
        thread_id: UUID,
        request_id: str | None,
    ) -> str:
        """Emit the sole terminal convention for a connected client."""
        event = StreamEventPayload(
            event_name="complete",
            data={"status": state.terminal_status},
        )
        return self._encode_event(event, state, thread_id, request_id)

    @staticmethod
    def _next_envelope(
        data: JsonValue,
        state: _DeliveryState,
        thread_id: UUID,
        request_id: str | None,
    ) -> StreamEventEnvelope:
        """Allocate the next monotonic sequence number inside one thread stream."""
        state.sequence += 1
        return StreamEventEnvelope(
            thread_id=thread_id,
            sequence=state.sequence,
            request_id=request_id,
            data=data,
        )

    def _map_error_safely(
        self,
        exc: Exception,
        thread_id: UUID,
        request_id: str | None,
    ) -> StreamEventPayload:
        """Prevent an application error mapper from breaking stream termination."""
        try:
            return self._error_mapper(exc)
        except Exception:
            logger.exception(
                "SSE error mapper failed",
                extra={"thread_id": str(thread_id), "request_id": request_id},
            )
            return _default_error_event(exc)

    @staticmethod
    async def _close_source(
        iterator: AsyncIterator[StreamEventPayload],
        state: _DeliveryState,
        thread_id: UUID,
    ) -> None:
        """Cancel the outstanding read and close the source on every exit path."""
        if state.pending_read is not None and not state.pending_read.done():
            state.pending_read.cancel()
            await asyncio.gather(state.pending_read, return_exceptions=True)
        if isinstance(iterator, AsyncClosable):
            try:
                await iterator.aclose()
            except Exception:
                logger.exception(
                    "Failed to close SSE source iterator",
                    extra={"thread_id": str(thread_id)},
                )


def _default_error_event(exc: Exception) -> StreamEventPayload:
    """Hide exception text while preserving a stable generic client contract."""
    del exc
    return StreamEventPayload(
        event_name="error",
        data={
            "code": "STREAM_DELIVERY_ERROR",
            "message": "The stream ended unexpectedly.",
        },
    )
