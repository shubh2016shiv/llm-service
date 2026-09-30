"""ASGI disconnect regressions for eagerly reserved inference streams.

Architecture:
    simulated ASGI transport -> managed response -> real session + capacity limiter
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from starlette.requests import ClientDisconnect

from app.schemas.responses_schema import ChatStreamChunk
from app.services.streaming_session import StreamingInferenceSession
from app.streaming.chat_chunk_adapter import adapt_chat_chunks
from app.streaming.managed_response import ManagedStreamingResponse
from app.streaming.sse_delivery import SSEStreamDelivery
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class RecordingFinalizer:
    """Record terminal accounting independently of HTTP delivery."""

    def __init__(self):
        self.statuses = []

    async def __call__(self, status, prompt_tokens, completion_tokens):
        self.statuses.append(status)


async def provider() -> AsyncIterator[ChatStreamChunk]:
    yield ChatStreamChunk(content="hello")
    await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_message", ["http.response.start", "http.response.body"])
async def test_response_send_failure_releases_reservation_and_capacity(failure_message):
    """REQ: failed headers and failed body delivery must both close eager resources."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    finalizer = RecordingFinalizer()
    session = StreamingInferenceSession(
        provider_chunks=provider(),
        lease=await limiter.acquire(),
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )
    delivery = SSEStreamDelivery(heartbeat_interval_seconds=1)
    response = ManagedStreamingResponse(
        delivery.stream(adapt_chat_chunks(session), thread_id=uuid4()),
        source=session,
    )

    async def send(message):
        if message["type"] == failure_message:
            raise OSError("client socket closed")

    async def receive():
        await asyncio.Event().wait()

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    assert finalizer.statuses == ["disconnected"]
    assert limiter.active_stream_count == 0


@pytest.mark.asyncio
async def test_response_legacy_asgi_disconnect_before_headers_releases_resources():
    """REQ: cancellation by Starlette's disconnect task also closes unstarted sources."""
    limiter = WorkerStreamCapacityLimiter(max_concurrent=1, retry_after_seconds=1)
    finalizer = RecordingFinalizer()
    session = StreamingInferenceSession(
        provider_chunks=provider(),
        lease=await limiter.acquire(),
        finalize=finalizer,
        cleanup_timeout_seconds=1,
    )
    response = ManagedStreamingResponse(
        SSEStreamDelivery(heartbeat_interval_seconds=1).stream(
            adapt_chat_chunks(session),
            thread_id=uuid4(),
        ),
        source=session,
    )

    async def send(message):
        await asyncio.Event().wait()

    async def receive():
        return {"type": "http.disconnect"}

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)

    assert finalizer.statuses == ["disconnected"]
    assert limiter.active_stream_count == 0
