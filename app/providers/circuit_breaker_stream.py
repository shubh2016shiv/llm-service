"""Bridge async-generator streams through a coroutine circuit breaker.

Architecture:
    BaseProvider.stream_generate -> CircuitBreakerStream -> provider generator

``aiobreaker`` guards coroutines, not async generators. This bridge consumes a
generator in one guarded task and uses a one-item queue so slow HTTP clients
still apply backpressure to the upstream provider stream.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiobreaker import CircuitBreakerError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import aiobreaker


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    """Carry a producer exception across the typed queue boundary."""

    exception: Exception


class _StreamFinished:
    """Unambiguous end-of-stream sentinel type."""


_STREAM_FINISHED = _StreamFinished()


class CircuitBreakerStream[ChunkT]:
    """Consume one provider stream under breaker control with backpressure."""

    def __init__(
        self,
        source: AsyncIterator[ChunkT],
        circuit_breaker: aiobreaker.CircuitBreaker,
        translate_circuit_error: Callable[[CircuitBreakerError], Exception],
        translate_error: Callable[[Exception], Exception],
    ) -> None:
        """Store one single-use source and its error translators."""
        self._source = source
        self._circuit_breaker = circuit_breaker
        self._translate_circuit_error = translate_circuit_error
        self._translate_error = translate_error
        self._queue: asyncio.Queue[ChunkT | _StreamFailure | _StreamFinished] = asyncio.Queue(1)

    async def iterate(self) -> AsyncIterator[ChunkT]:
        """Yield guarded chunks and deterministically stop the producer."""
        producer = asyncio.create_task(self._produce(), name="provider-stream")
        try:
            async for chunk in self._read_queue():
                yield chunk
        finally:
            await self._stop_producer(producer)

    async def _produce(self) -> None:
        """Run source consumption as the single breaker-visible coroutine."""
        try:
            await self._circuit_breaker.call_async(self._consume_source)
        except asyncio.CancelledError:
            raise
        except CircuitBreakerError as exc:
            await self._queue.put(_StreamFailure(self._translate_circuit_error(exc)))
        except Exception as exc:
            await self._queue.put(_StreamFailure(self._translate_error(exc)))
        else:
            await self._queue.put(_STREAM_FINISHED)

    async def _consume_source(self) -> None:
        """Move source chunks into the bounded queue."""
        async for chunk in self._source:
            await self._queue.put(chunk)

    async def _read_queue(self) -> AsyncIterator[ChunkT]:
        """Turn internal queue signals back into generator semantics."""
        while True:
            item = await self._queue.get()
            if isinstance(item, _StreamFinished):
                return
            if isinstance(item, _StreamFailure):
                raise item.exception
            yield item

    @staticmethod
    async def _stop_producer(producer: asyncio.Task[None]) -> None:
        """Cancel unfinished upstream work when the consumer disconnects."""
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
        else:
            await producer
