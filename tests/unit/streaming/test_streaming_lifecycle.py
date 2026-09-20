"""Concurrency, cancellation, and wire-contract tests for SSE streaming."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from app.schemas.responses_schema import ChatStreamChunk, Usage
from app.services.stream_session import StreamingInferenceSession
from app.streaming.admission import (
    StreamAdmissionController,
    StreamCapacityExceededError,
)
from app.streaming.encoder import encode_event
from app.streaming.events import SSEEvent
from app.streaming.transport import encode_sse_stream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.stream_session import StreamTerminalStatus


class RecordingFinalizer:
    """Record finalization attempts without external services."""

    def __init__(self) -> None:
        self.calls: list[tuple[StreamTerminalStatus, int | None, int | None]] = []

    async def __call__(
        self,
        status: StreamTerminalStatus,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        self.calls.append((status, prompt_tokens, completion_tokens))


async def _completed_provider() -> AsyncIterator[ChatStreamChunk]:
    yield ChatStreamChunk(content="hello")
    yield ChatStreamChunk(
        finish_reason="stop",
        usage=Usage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )


async def _blocked_provider(started: asyncio.Event) -> AsyncIterator[ChatStreamChunk]:
    started.set()
    await asyncio.Event().wait()
    yield ChatStreamChunk(content="unreachable")


async def _failed_provider() -> AsyncIterator[ChatStreamChunk]:
    yield ChatStreamChunk(content="partial")
    raise RuntimeError("provider connection failed")


@pytest.mark.asyncio
async def test_session_finalizes_completed_usage_exactly_once() -> None:
    """Normal exhaustion reconciles usage and returns its admission slot."""
    admission = StreamAdmissionController(max_concurrent=1, retry_after_seconds=1)
    lease = await admission.acquire()
    finalizer = RecordingFinalizer()
    session = StreamingInferenceSession(
        provider_chunks=_completed_provider(),
        lease=lease,
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )

    chunks = [chunk async for chunk in session]
    await session.aclose()

    assert [chunk.content for chunk in chunks] == ["hello", ""]
    assert finalizer.calls == [("completed", 4, 2)]
    assert admission.active == 0


@pytest.mark.asyncio
async def test_cancellation_finalizes_disconnected_and_releases_capacity() -> None:
    """Client cancellation cannot leak provider work or stream capacity."""
    admission = StreamAdmissionController(max_concurrent=1, retry_after_seconds=1)
    lease = await admission.acquire()
    finalizer = RecordingFinalizer()
    started = asyncio.Event()
    session = StreamingInferenceSession(
        provider_chunks=_blocked_provider(started),
        lease=lease,
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )

    pending_chunk = asyncio.create_task(anext(session))
    await started.wait()
    pending_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_chunk

    assert finalizer.calls == [("disconnected", None, None)]
    assert admission.active == 0


@pytest.mark.asyncio
async def test_close_before_first_chunk_still_finalizes_and_releases_capacity() -> None:
    """A disconnect in the pre-first-token window cannot leak a reservation."""
    admission = StreamAdmissionController(max_concurrent=1, retry_after_seconds=1)
    lease = await admission.acquire()
    finalizer = RecordingFinalizer()
    session = StreamingInferenceSession(
        provider_chunks=_completed_provider(),
        lease=lease,
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )

    await session.aclose()

    assert finalizer.calls == [("disconnected", None, None)]
    assert admission.active == 0


@pytest.mark.asyncio
async def test_provider_failure_finalizes_failed_exactly_once() -> None:
    """A mid-stream provider error records failure and returns capacity."""
    admission = StreamAdmissionController(max_concurrent=1, retry_after_seconds=1)
    lease = await admission.acquire()
    finalizer = RecordingFinalizer()
    session = StreamingInferenceSession(
        provider_chunks=_failed_provider(),
        lease=lease,
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )

    first = await anext(session)
    with pytest.raises(RuntimeError, match="provider connection failed"):
        await anext(session)
    await session.aclose()

    assert first.content == "partial"
    assert finalizer.calls == [("failed", None, None)]
    assert admission.active == 0


@pytest.mark.asyncio
async def test_admission_fails_fast_at_worker_limit() -> None:
    """A saturated worker rejects instead of accumulating waiting sockets."""
    admission = StreamAdmissionController(max_concurrent=1, retry_after_seconds=3)
    lease = await admission.acquire()

    with pytest.raises(StreamCapacityExceededError) as exc_info:
        await admission.acquire()

    assert exc_info.value.retry_after_seconds == 3
    await lease.release()
    assert admission.active == 0


@pytest.mark.asyncio
async def test_admission_accounting_is_safe_for_thousands_of_leases() -> None:
    """Concurrent acquisition and release retain exact accounting at scale."""
    connection_count = 2_000
    admission = StreamAdmissionController(
        max_concurrent=connection_count,
        retry_after_seconds=1,
    )

    leases = await asyncio.gather(
        *(admission.acquire() for _ in range(connection_count))
    )
    assert admission.active == connection_count

    await asyncio.gather(*(lease.release() for lease in leases))
    assert admission.active == 0


@pytest.mark.asyncio
async def test_transport_emits_heartbeats_chunks_and_terminal_events() -> None:
    """Quiet providers receive heartbeats without cancelling their pending read."""

    async def delayed_provider() -> AsyncIterator[ChatStreamChunk]:
        await asyncio.sleep(0.03)
        yield ChatStreamChunk(content="hello")

    wire_events = [
        event
        async for event in encode_sse_stream(
            delayed_provider(),
            heartbeat_interval_seconds=0.01,
            request_id="request-1",
        )
    ]

    assert any(event == ": heartbeat\n\n" for event in wire_events)
    assert any("id: request-1:1" in event and "event: chunk" in event for event in wire_events)
    assert wire_events[-2].startswith("event: complete")
    assert wire_events[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_transport_reports_failed_terminal_state_after_provider_error() -> None:
    """Once headers are sent, failures remain machine-readable SSE events."""
    wire_events = [
        event
        async for event in encode_sse_stream(
            _failed_provider(),
            heartbeat_interval_seconds=1,
        )
    ]

    assert any("event: error" in event for event in wire_events)
    assert 'data: {"status":"failed"}' in wire_events[-2]
    assert wire_events[-1] == "data: [DONE]\n\n"


def test_encoder_supports_multiline_data_without_invalid_frames() -> None:
    """Every data line receives its own SSE field prefix."""
    encoded = encode_event(SSEEvent(event="message", data="one\ntwo", event_id="7"))

    assert encoded == "id: 7\nevent: message\ndata: one\ndata: two\n\n"
    payload = json.dumps({"encoded": encoded})
    assert "data: one" in payload
