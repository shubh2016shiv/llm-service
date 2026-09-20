"""Business lifecycle for one reserved provider-stream execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from app.schemas.responses_schema import ChatStreamChunk
from app.streaming.usage import StreamUsageAccumulator

if TYPE_CHECKING:
    from app.streaming.admission import StreamLease

logger = logging.getLogger(__name__)

StreamTerminalStatus = Literal["completed", "failed", "cancelled", "disconnected"]
FinalizeCallback = Callable[[StreamTerminalStatus, int | None, int | None], Awaitable[None]]


class StreamingInferenceSession(AsyncIterator[ChatStreamChunk]):
    """Own provider iteration, usage collection, and exact-once cleanup.

    The explicit iterator object is intentional. Unlike an async generator,
    ``aclose`` can finalize a reservation even if the response is disconnected
    before the provider yields its first chunk.
    """

    def __init__(
        self,
        *,
        provider_chunks: AsyncIterator[ChatStreamChunk],
        lease: StreamLease,
        finalize: FinalizeCallback,
        cleanup_timeout_seconds: float,
    ) -> None:
        self._provider_chunks = provider_chunks.__aiter__()
        self._lease = lease
        self._finalize_callback = finalize
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._usage = StreamUsageAccumulator()
        self._closed = False
        self._finish_lock = asyncio.Lock()

    def __aiter__(self) -> StreamingInferenceSession:
        return self

    async def __anext__(self) -> ChatStreamChunk:
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
        """Close the provider, finalize quota, and release admission once."""
        async with self._finish_lock:
            if self._closed:
                return
            self._closed = True
            cleanup_task = asyncio.create_task(self._cleanup(status))
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # A second cancellation must not orphan quota or admission state.
                await cleanup_task
                raise

    async def _cleanup(self, status: StreamTerminalStatus) -> None:
        """Run bounded provider cleanup before quota and admission cleanup."""
        try:
            close = getattr(self._provider_chunks, "aclose", None)
            if close is not None:
                async with asyncio.timeout(self._cleanup_timeout_seconds):
                    await close()
        except TimeoutError:
            logger.warning("Provider stream cleanup timed out")
        except Exception:
            logger.exception("Provider stream cleanup failed")
        try:
            await self._finalize_callback(
                status,
                self._usage.prompt_tokens,
                self._usage.completion_tokens,
            )
        finally:
            await self._lease.release()
