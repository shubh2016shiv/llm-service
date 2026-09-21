"""Specification tests for inference request and normalized response contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.auth_schema import AuthTokenPayload
from app.schemas.requests_schema import ChatMessage, ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses_schema import ChatResponse, ChatStreamChunk, EmbedResponse, Usage


def test_chat_message_with_provider_specific_extra_field_is_rejected() -> None:
    """REQ: clients cannot bypass the gateway contract with provider-native fields."""
    payload = {"role": "assistant", "content": "done", "tool_calls": []}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ChatMessage.model_validate(payload)


def test_chat_message_with_empty_content_is_rejected() -> None:
    """REQ: a text-only message must contain text."""
    with pytest.raises(ValidationError, match="at least 1 character"):
        ChatMessage(role="user", content="")


def test_chat_request_without_thread_id_is_rejected() -> None:
    """REQ: every chat turn belongs to an explicit, client-visible conversation."""
    payload = {"messages": [{"role": "user", "content": "hello"}]}

    with pytest.raises(ValidationError, match="thread_id"):
        ChatRequest.model_validate(payload)


def test_embed_request_with_empty_batch_item_is_rejected() -> None:
    """REQ: every batch element must be meaningful provider input."""
    with pytest.raises(ValidationError, match="at least 1 character"):
        EmbedRequest(input=["valid", ""])


def test_rerank_request_with_top_n_larger_than_documents_is_rejected() -> None:
    """REQ: requested result count cannot exceed the candidate count."""
    with pytest.raises(ValidationError, match="top_n cannot exceed"):
        RerankRequest(query="query", documents=["one"], top_n=2)


def test_usage_with_inconsistent_total_is_rejected() -> None:
    """REQ: normalized token accounting cannot contradict itself."""
    with pytest.raises(ValidationError, match="total_tokens must equal"):
        Usage(prompt_tokens=3, completion_tokens=2, total_tokens=99)


def test_embed_response_with_inconsistent_dimensions_is_rejected() -> None:
    """REQ: every vector in one embedding response has the same dimensionality."""
    with pytest.raises(ValidationError, match="same dimensions"):
        EmbedResponse(embeddings=[[0.1, 0.2], [0.3]])


def test_provider_debug_payloads_are_excluded_from_serialization() -> None:
    """REQ: internal provider payloads never cross the public API boundary."""
    response = ChatResponse(content="hello", raw_response={"secret_header": "value"})
    chunk = ChatStreamChunk(content="hel", raw_chunk={"internal": True})

    serialized_response = response.model_dump()
    serialized_chunk = chunk.model_dump()

    assert "raw_response" not in serialized_response
    assert "raw_chunk" not in serialized_chunk


def test_auth_token_with_impossible_time_window_is_rejected() -> None:
    """REQ: an access token must expire strictly after it was issued."""
    issued_at = datetime.now(UTC)

    with pytest.raises(ValidationError, match="expires_at must be later"):
        AuthTokenPayload(
            user_id=uuid4(),
            role="developer",
            token_id=uuid4(),
            issued_at=issued_at,
            expires_at=issued_at - timedelta(seconds=1),
        )


def test_auth_token_with_naive_datetime_is_rejected() -> None:
    """REQ: token timestamps carry timezone information to avoid local-time ambiguity."""
    issued_at = datetime.now()

    with pytest.raises(ValidationError, match="timezone info"):
        AuthTokenPayload(
            user_id=uuid4(),
            role="developer",
            token_id=uuid4(),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=5),
        )
