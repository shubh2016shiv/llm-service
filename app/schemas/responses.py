"""
app/schemas/responses.py — Outbound response schemas for the LLM gateway.

These Pydantic models represent responses flowing back from providers through
the dispatcher to the API layer.

Design rules (per Agents.md §4.6):
- Pydantic v2 with model_config = ConfigDict(...).
- frozen=False for responses (may be enriched by middleware).
- Every field has a type annotation and a docstring.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class Usage(BaseModel):
    """Token usage summary returned by most providers.

    Field names follow OpenAI conventions. Non-OpenAI providers normalize
    their native usage fields into this schema.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    prompt_tokens: int = Field(
        default=0,
        ge=0,
        description="Tokens consumed by the input/prompt.",
    )
    completion_tokens: int = Field(
        default=0,
        ge=0,
        description="Tokens generated in the completion.",
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="Sum of prompt_tokens + completion_tokens.",
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatResponse(BaseModel):
    """A complete (non-streaming) chat completion response.

    The `raw_response` field carries the provider-native response payload
    for debugging and audit purposes. It is excluded from API serialization
    by default.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    content: str = Field(
        ...,
        description="The assistant's response text.",
    )
    role: str = Field(
        default="assistant",
        description="Author role (almost always 'assistant').",
    )
    finish_reason: str | None = Field(
        default=None,
        description="Why the model stopped (e.g. 'stop', 'length', 'tool_calls').",
    )
    usage: Usage | None = Field(
        default=None,
        description="Token usage. None when the provider does not report usage.",
    )
    model: str = Field(
        default="",
        description="Model identifier the provider used to fulfill this request.",
    )
    raw_response: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-native response payload (excluded from API responses).",
        repr=False,
    )


class ChatStreamChunk(BaseModel):
    """A single chunk from a streaming chat completion.

    Streamed one-at-a-time via AsyncIterator[ChatStreamChunk] from the
    provider's stream_generate() method.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    content: str = Field(
        default="",
        description="Incremental text delta. Empty for non-content events.",
    )
    finish_reason: str | None = Field(
        default=None,
        description="Set on the final chunk when the stream ends (e.g. 'stop').",
    )
    index: int = Field(
        default=0,
        ge=0,
        description="Choice index (0 for single-choice streams).",
    )
    raw_chunk: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-native chunk payload (excluded from API responses).",
        repr=False,
    )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class EmbedResponse(BaseModel):
    """An embedding response containing one or more embedding vectors."""

    model_config = ConfigDict(extra="allow", frozen=True)

    embeddings: list[list[float]] = Field(
        ...,
        description="List of embedding vectors. Each vector is a list of floats.",
    )
    model: str = Field(
        default="",
        description="Model identifier the provider used.",
    )
    usage: Usage | None = Field(
        default=None,
        description="Token usage for the embedding request. None if not reported.",
    )


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------


class RerankResult(BaseModel):
    """A single ranked document with its relevance score."""

    model_config = ConfigDict(extra="allow", frozen=True)

    index: int = Field(
        ...,
        ge=0,
        description="Original document index in the request.",
    )
    document: str = Field(
        ...,
        description="The document text.",
    )
    relevance_score: float = Field(
        ...,
        description="Relevance score (higher = more relevant). Provider-dependent range.",
    )


class RerankResponse(BaseModel):
    """A re-rank response containing ordered results."""

    model_config = ConfigDict(extra="allow", frozen=True)

    results: list[RerankResult] = Field(
        ...,
        description="Documents ordered by relevance (highest first).",
    )
    model: str = Field(
        default="",
        description="Model identifier the provider used.",
    )
    usage: Usage | None = Field(
        default=None,
        description="Token usage for the rerank request. None if not reported.",
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthStatus(BaseModel):
    """Health-check result for a single provider deployment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str = Field(
        ...,
        description="Provider name (e.g. 'openai', 'bedrock').",
    )
    healthy: bool = Field(
        ...,
        description="True if the provider responded successfully.",
    )
    latency_ms: int = Field(
        ...,
        ge=0,
        description="Round-trip latency in milliseconds.",
    )
    detail: str | None = Field(
        default=None,
        description="Error detail when unhealthy; None otherwise.",
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

# Union type for all possible responses — used in type signatures where a
# single code path may return one of several response types.
ResponseUnion = ChatResponse | EmbedResponse | RerankResponse | HealthStatus
