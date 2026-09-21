"""Own the business lifecycle of one reserved provider stream.

Architecture:
    InferenceService
        -> StreamingInferenceSession
        -> provider iterator + quota finalizer + capacity lease

This service-layer iterator is deliberately transport-independent. SSE,
WebSocket, or gRPC consumers receive the same exact-once usage reconciliation
and cleanup behavior.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from app.schemas.responses_schema import ChatStreamChunk
from app.services.stream_usage import StreamUsageAccumulator

if TYPE_CHECKING:
    from app.streaming.stream_capacity import StreamCapacityLease

logger = logging.getLogger(__name__)

StreamTerminalStatus = Literal["completed", "failed", "cancelled", "disconnected"]
FinalizeCallback = Callable[[StreamTerminalStatus, int | None, int | None], Awaitable[None]]


class StreamingInferenceSession(AsyncIterator[ChatStreamChunk]):
    """Collect usage and finalize provider, quota, and capacity exactly once.

    An explicit iterator object is important here. Its ``aclose`` method can
    finalize resources even when a client disconnects before the first provider
    chunk—an edge case that a never-started async generator cannot clean up.
    """

    def __init__(
        self,
        *,
        provider_chunks: AsyncIterator[ChatStreamChunk],
        lease: StreamCapacityLease,
        finalize: FinalizeCallback,
        cleanup_timeout_seconds: float,
    ) -> None:
        """Capture the resources whose lifetime equals the client stream."""
        self._provider_chunks = provider_chunks.__aiter__()
        self._lease = lease
        self._finalize_callback = finalize
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._usage = StreamUsageAccumulator()
        self._closed = False
        self._finish_lock = asyncio.Lock()

    def __aiter__(self) -> StreamingInferenceSession:
        """Return this stateful object as its own async iterator."""
        return self

    async def __anext__(self) -> ChatStreamChunk:
        """Read one provider chunk and classify every terminal path."""
        if self._closed:
            raise StopAsyncIteration
        try:
            chunk = await anext(self._provider_chunks)
        except StopAsyncIteration:
            await self._finish("completed")
            raise
        except asyncio.CancelledError:
            await self._finish("disconnected")
            raise
        except Exception:
            await self._finish("failed")
            raise
        self._usage.observe(chunk.usage)
        return chunk

    async def aclose(self) -> None:
        """Treat consumer-side early closure as a client disconnect."""
        await self._finish("disconnected")

    async def _finish(self, status: StreamTerminalStatus) -> None:
        """Run cleanup once, shielding it from the disconnect cancellation."""
        async with self._finish_lock:
            if self._closed:
                return
            self._closed = True
            cleanup_task = asyncio.create_task(self._cleanup(status))
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise

    async def _cleanup(self, status: StreamTerminalStatus) -> None:
        """Close the provider before reconciling quota and releasing capacity."""
        await self._close_provider()
        try:
            await self._finalize_callback(
                status,
                self._usage.prompt_tokens,
                self._usage.completion_tokens,
            )
        finally:
            await self._lease.release()

    async def _close_provider(self) -> None:
        """Bound provider cleanup so one broken iterator cannot retain a slot."""
        close = getattr(self._provider_chunks, "aclose", None)
        if close is None:
            return
        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                await close()
        except TimeoutError:
            logger.warning("Provider stream cleanup timed out")
        except Exception:
            logger.exception("Provider stream cleanup failed")
