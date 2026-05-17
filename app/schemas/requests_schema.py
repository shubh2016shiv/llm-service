"""
app/schemas/requests_schema.py — Inbound request schemas for the LLM gateway.

These Pydantic models represent requests entering the gateway from the API
layer. They flow through: API router → request_dispatcher → provider.

Design rules (per Agents.md §4.6):
- Pydantic v2 with model_config = ConfigDict(...).
- Every field has a type annotation and a docstring.
- Use discriminators or Literal unions where appropriate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single message in a chat conversation.

    Mirrors the OpenAI message schema: {role, content, name?}.
    """

    model_config = ConfigDict(extra="allow", frozen=False)

    role: Literal["system", "user", "assistant"] = Field(
        ...,
        description="Message author role.",
    )
    content: str = Field(
        ...,
        min_length=0,
        description="Message body. Empty strings are allowed for tool-call scenarios.",
    )
    name: str | None = Field(
        default=None,
        description="Optional participant name (function name / username).",
    )


class ChatRequest(BaseModel):
    """A chat completion request dispatched to a provider."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    messages: list[ChatMessage] = Field(
        ...,
        min_length=1,
        description="Ordered conversation history. At least one message required.",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Provider defaults apply when None.",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Maximum completion tokens. Provider/deployment defaults apply when None.",
    )
    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling probability. Provider defaults apply when None.",
    )
    stop: list[str] | None = Field(
        default=None,
        description="Stop sequences that halt generation. Provider defaults apply when None.",
    )
    stream: bool = Field(
        default=False,
        description="When true, the response is a server-sent event stream rather than a JSON body.",
    )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class EmbedRequest(BaseModel):
    """An embedding request dispatched to a provider."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    input: str | list[str] = Field(
        ...,
        min_length=1,
        description="Single text or list of texts to embed.",
    )


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------


class RerankRequest(BaseModel):
    """A re-rank request dispatched to a provider.

    Not all providers support rerank natively; unsupported providers raise a
    ProviderError with a descriptive message.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    query: str = Field(
        ...,
        min_length=1,
        description="Search query to rank documents against.",
    )
    documents: list[str] = Field(
        ...,
        min_length=1,
        description="Candidate documents to re-rank.",
    )
    top_n: int | None = Field(
        default=None,
        ge=1,
        description="Return only the top-N documents. Returns all when None.",
    )
