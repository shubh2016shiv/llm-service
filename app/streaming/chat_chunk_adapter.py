"""Adapt this service's normalized chat chunks to portable stream events.

Architecture:
    provider ChatStreamChunk
        -> adapt_chat_chunks
        -> StreamEventPayload
        -> SSEStreamDelivery

This is the only SSE module that knows the application's chat schema. The core
event and SSE modules remain reusable for structured-output or non-LLM sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import LLMServiceError

from .sse_delivery import AsyncClosable
from .stream_event import StreamEventPayload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.schemas.responses_schema import ChatStreamChunk


async def adapt_chat_chunks(
    chunks: AsyncIterator[ChatStreamChunk],
) -> AsyncIterator[StreamEventPayload]:
    """Translate normalized provider chunks and preserve source cleanup."""
    iterator = chunks.__aiter__()
    try:
        async for chunk in iterator:
            yield chat_chunk_to_event(chunk)
    finally:
        if isinstance(iterator, AsyncClosable):
            await iterator.aclose()


def chat_chunk_to_event(chunk: ChatStreamChunk) -> StreamEventPayload:
    """Represent text-bearing chunks separately from usage/finish metadata."""
    event_name = "text_delta" if chunk.content else "stream_metadata"
    return StreamEventPayload(
        event_name=event_name,
        data=chunk.model_dump(mode="json", exclude={"raw_chunk"}, exclude_none=True),
    )


def map_llm_stream_error(exc: Exception) -> StreamEventPayload:
    """Expose stable domain codes without leaking raw provider exception text."""
    error_code = exc.error_code if isinstance(exc, LLMServiceError) else "STREAM_DELIVERY_ERROR"
    return StreamEventPayload(
        event_name="error",
        data={
            "code": error_code,
            "message": "The stream ended before completion.",
        },
    )
