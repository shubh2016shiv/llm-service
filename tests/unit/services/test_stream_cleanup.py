"""Resource ownership regressions for stream termination.

Architecture:
    Tests -> StreamingInferenceSession -> controlled provider and quota boundaries
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.schemas.responses_schema import ChatStreamChunk
from app.services.streaming_session import StreamingInferenceSession
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class ControlledFinalizer:
    """Expose accounting checkpoints without sleeps or external services."""

    def __init__(self, *, fail: bool = False) -> None:
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.finished = asyncio.Event()
        self.calls = 0
        self.fail = fail

    async def __call__(self, status, prompt_tokens, completion_tokens) -> None:
        self.calls += 1
        self.started.set()
        await self.proceed.wait()
        self.finished.set()
        if self.fail:
            raise RuntimeError("accounting unavailable")


async def empty_provider() -> AsyncIterator[ChatStreamChunk]:
    """Finish without generating content."""
    if False:
        yield ChatStreamChunk(content="unused")


async def build_session(finalizer, *, timeout: float = 1):
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    session = StreamingInferenceSession(
        provider_chunks=empty_provider(),
        lease=await limiter.acquire(),
        finalize=finalizer,
        cleanup_timeout_seconds=timeout,
    )
    return session, limiter


@pytest.mark.asyncio
async def test_close_repeated_cancellation_finishes_accounting_and_releases_capacity():
    """REQ: repeated disconnect cancellation cannot cancel owned accounting."""
    finalizer = ControlledFinalizer()
    session, limiter = await build_session(finalizer)
    closing = asyncio.create_task(session.aclose())
    await finalizer.started.wait()

    closing.cancel()
    # Schedule the second cancellation after the first has reached its waiter.
    asyncio.get_running_loop().call_soon(closing.cancel)
    asyncio.get_running_loop().call_soon(finalizer.proceed.set)
    with pytest.raises(asyncio.CancelledError):
        await closing
    await session.aclose()

    assert finalizer.finished.is_set()
    assert finalizer.calls == 1
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_close_stalled_accounting_releases_capacity_within_cleanup_budget():
    """REQ: a stalled token manager cannot hold worker capacity indefinitely."""
    finalizer = ControlledFinalizer()
    session, limiter = await build_session(finalizer, timeout=0.01)

    await asyncio.wait_for(session.aclose(), timeout=1)

    assert finalizer.calls == 1
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_exhaustion_accounting_failure_does_not_report_success():
    """REQ: successful provider exhaustion must not hide failed accounting."""
    finalizer = ControlledFinalizer(fail=True)
    finalizer.proceed.set()
    session, limiter = await build_session(finalizer)

    with pytest.raises(RuntimeError, match="accounting unavailable"):
        await anext(session)

    assert finalizer.calls == 1
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_close_concurrent_callers_wait_for_same_finalization():
    """REQ: every close waiter observes completion of the one owned cleanup."""
    finalizer = ControlledFinalizer()
    session, limiter = await build_session(finalizer)
    first = asyncio.create_task(session.aclose())
    await finalizer.started.wait()
    second = asyncio.create_task(session.aclose())

    finalizer.proceed.set()
    await asyncio.gather(first, second)

    assert finalizer.calls == 1
    assert finalizer.finished.is_set()
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1])
async def test_session_invalid_cleanup_timeout_is_rejected(timeout):
    """REQ: a nonpositive cleanup budget is a configuration error."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    lease = await limiter.acquire()

    with pytest.raises(ValueError, match="cleanup_timeout_seconds"):
        StreamingInferenceSession(
            provider_chunks=empty_provider(),
            lease=lease,
            finalize=ControlledFinalizer(),
            cleanup_timeout_seconds=timeout,
        )

    await lease.release()
