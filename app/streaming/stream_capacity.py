"""Bound concurrent long-lived streams inside one application worker.

Architecture:
    InferenceService -> WorkerStreamCapacityLimiter -> StreamCapacityLease

This is a fail-fast bulkhead. It protects the event loop and provider HTTP pool
without adding Redis to the token-delivery path. A lease represents ownership
of exactly one slot and makes release idempotent across competing cleanup paths.
"""

from __future__ import annotations

import asyncio

from app.core.exceptions import StreamCapacityExceededError


class StreamCapacityLease:
    """Release one worker stream slot at most once."""

    def __init__(self, limiter: WorkerStreamCapacityLimiter) -> None:
        """Retain the limiter that owns this lease's slot."""
        self._limiter = limiter
        self._released = False
        self._lock = asyncio.Lock()

    async def release(self) -> None:
        """Return the slot once even when multiple cleanup paths race."""
        async with self._lock:
            if self._released:
                return
            self._released = True
            await self._limiter._release()


class WorkerStreamCapacityLimiter:
    """Reject new streams when this process reaches its configured limit.

    The limit is intentionally process-local. Horizontal replicas add capacity
    without a shared coordination round trip for every token stream.
    """

    def __init__(self, *, max_concurrent: int, retry_after_seconds: int) -> None:
        """Configure a positive stream limit and retry hint."""
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        if retry_after_seconds < 1:
            raise ValueError("retry_after_seconds must be positive")
        self._max_concurrent = max_concurrent
        self._retry_after_seconds = retry_after_seconds
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active_stream_count(self) -> int:
        """Return the number of slots currently owned by open streams."""
        return self._active

    async def acquire(self) -> StreamCapacityLease:
        """Acquire immediately or raise instead of queueing an open socket."""
        async with self._lock:
            if self._active >= self._max_concurrent:
                raise StreamCapacityExceededError(
                    self._max_concurrent,
                    self._retry_after_seconds,
                )
            self._active += 1
        return StreamCapacityLease(self)

    async def _release(self) -> None:
        """Return a slot; only ``StreamCapacityLease`` calls this method."""
        async with self._lock:
            self._active -= 1
            if self._active < 0:
                raise RuntimeError("stream capacity accounting became negative")
