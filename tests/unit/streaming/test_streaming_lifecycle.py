"""Lifecycle, backpressure, and wire-contract tests for reusable SSE streaming."""

from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from app.core.exceptions import StreamCapacityExceededError
from app.schemas.responses_schema import ChatStreamChunk, Usage
from app.services.streaming_session import StreamingInferenceSession
from app.streaming.sse_delivery import SSEStreamDelivery
from app.streaming.sse_encoder import encode_sse_message
from app.streaming.sse_message import SSEMessage
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter
from app.streaming.stream_event import (
    StreamEventPayload,
    StructuredOutputDelta,
    structured_delta_event,
    text_delta_event,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.streaming_session import StreamTerminalStatus

THREAD_ID = UUID("70000000-0000-0000-0000-000000000001")


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


def _json_data(message: str) -> dict[str, object]:
    data = "\n".join(
        line.removeprefix("data: ")
        for line in message.splitlines()
        if line.startswith("data: ")
    )
    payload = json.loads(data)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_session_finalizes_completed_usage_exactly_once() -> None:
    """REQ: normal exhaustion reconciles usage and returns its worker slot."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    lease = await limiter.acquire()
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
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_session_cancellation_releases_every_owned_resource() -> None:
    """REQ: a disconnected client cannot leak quota or worker capacity."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    lease = await limiter.acquire()
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
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_session_close_before_first_chunk_still_finalizes() -> None:
    """REQ: closure in the pre-first-token window releases the reservation."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    lease = await limiter.acquire()
    finalizer = RecordingFinalizer()
    session = StreamingInferenceSession(
        provider_chunks=_completed_provider(),
        lease=lease,
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )

    await session.aclose()

    assert finalizer.calls == [("disconnected", None, None)]
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_session_provider_failure_finalizes_failed_once() -> None:
    """REQ: a mid-stream upstream error has one failed finalization."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    lease = await limiter.acquire()
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
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_capacity_limiter_fails_fast_at_worker_limit() -> None:
    """REQ: saturated workers reject instead of accumulating waiting sockets."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=3)
    lease = await limiter.acquire()

    with pytest.raises(StreamCapacityExceededError) as exc_info:
        await limiter.acquire()

    assert exc_info.value.retry_after_seconds == 3
    await lease.release()
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_capacity_accounting_is_exact_for_thousands_of_leases() -> None:
    """REQ: concurrent acquisition and release retain exact accounting."""
    connection_count = 2_000
    limiter = WorkerStreamCapacityLimiter(
        max_concurrent=connection_count,
        retry_after_seconds=1,
    )

    leases = await asyncio.gather(*(limiter.acquire() for _ in range(connection_count)))
    assert limiter.active_stream_count == connection_count

    await asyncio.gather(*(lease.release() for lease in leases))
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_delivery_associates_every_data_event_with_thread() -> None:
    """REQ: chunks and completion share one stable thread and increasing sequence."""

    async def events() -> AsyncIterator[StreamEventPayload]:
        await asyncio.sleep(0.03)
        yield text_delta_event("hello")

    delivery = SSEStreamDelivery(heartbeat_interval_seconds=0.01)
    messages = [
        message
        async for message in delivery.stream(
            events(),
            thread_id=THREAD_ID,
            request_id="request-1",
        )
    ]

    assert any(message == ": heartbeat\n\n" for message in messages)
    data_messages = [message for message in messages if "data: " in message]
    payloads = [_json_data(message) for message in data_messages]
    assert [payload["thread_id"] for payload in payloads] == [str(THREAD_ID)] * 2
    assert [payload["sequence"] for payload in payloads] == [1, 2]
    assert "event: text_delta" in data_messages[0]
    assert "event: complete" in data_messages[1]
    assert all("[DONE]" not in message for message in messages)


@pytest.mark.asyncio
async def test_delivery_supports_parsed_structured_output_deltas() -> None:
    """REQ: structured fields can be assembled without sending invalid JSON fragments."""

    async def events() -> AsyncIterator[StreamEventPayload]:
        yield structured_delta_event(
            StructuredOutputDelta(
                operation="replace",
                path="/customer/name",
                value="Ada",
            )
        )

    delivery = SSEStreamDelivery(heartbeat_interval_seconds=1)
    messages = [
        message async for message in delivery.stream(events(), thread_id=THREAD_ID)
    ]

    payload = _json_data(messages[0])
    assert "event: structured_delta" in messages[0]
    assert payload["thread_id"] == str(THREAD_ID)
    assert payload["data"] == {
        "operation": "replace",
        "path": "/customer/name",
        "value": "Ada",
    }


@pytest.mark.asyncio
async def test_delivery_applies_backpressure_without_read_ahead_queue() -> None:
    """REQ: pausing the consumer prevents the producer from advancing."""
    producer_reads = 0

    async def events() -> AsyncIterator[StreamEventPayload]:
        nonlocal producer_reads
        producer_reads += 1
        yield text_delta_event("one")
        producer_reads += 1
        yield text_delta_event("two")

    delivery = SSEStreamDelivery(heartbeat_interval_seconds=1)
    async with aclosing(delivery.stream(events(), thread_id=THREAD_ID)) as stream:
        first = await anext(stream)

        assert "event: text_delta" in first
        assert producer_reads == 1


@pytest.mark.asyncio
async def test_delivery_hides_unexpected_exception_text() -> None:
    """REQ: post-header failures never expose raw upstream exception details."""

    async def events() -> AsyncIterator[StreamEventPayload]:
        yield text_delta_event("partial")
        raise RuntimeError("secret provider diagnostic")

    delivery = SSEStreamDelivery(heartbeat_interval_seconds=1)
    messages = [
        message async for message in delivery.stream(events(), thread_id=THREAD_ID)
    ]

    assert any("event: error" in message for message in messages)
    assert all("secret provider diagnostic" not in message for message in messages)
    assert _json_data(messages[-1])["data"] == {"status": "failed"}


def test_encoder_supports_multiline_data() -> None:
    """REQ: every logical data line receives its own SSE field prefix."""
    message = SSEMessage(event_name="message", data="one\ntwo", event_id="7")

    encoded = encode_sse_message(message)

    assert encoded == "id: 7\nevent: message\ndata: one\ndata: two\n\n"


@pytest.mark.parametrize("field_name", ["event_name", "event_id"])
def test_message_rejects_newline_field_injection(field_name: str) -> None:
    """REQ: caller-controlled names and IDs cannot inject extra SSE fields."""
    with pytest.raises(ValueError, match="must not contain CR or LF"):
        if field_name == "event_name":
            SSEMessage(event_name="safe\nevent: injected")
        else:
            SSEMessage(event_id="safe\nevent: injected")
