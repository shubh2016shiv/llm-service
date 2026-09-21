"""
Response Schemas
================

Outbound response models returned by inference endpoints.

Enterprise Pattern: Stable Response Contract Pattern
    Provider-specific payloads are normalized into one predictable API shape.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class Usage(BaseModel):
    """Token usage summary returned by most providers.

    Field names follow OpenAI conventions. Non-OpenAI providers normalize
    their native usage fields into this schema.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

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

    @model_validator(mode="after")
    def validate_total(self) -> Usage:
        """Ensure accounting fields cannot contradict one another."""
        expected_total = self.prompt_tokens + self.completion_tokens
        if self.total_tokens != expected_total:
            raise ValueError(
                "total_tokens must equal prompt_tokens plus completion_tokens"
            )
        return self


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatResponse(BaseModel):
    """A complete (non-streaming) chat completion response.

    The `raw_response` field carries the provider-native response payload
    for debugging and audit purposes. It is excluded from API serialization
    by default.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str = Field(
        ...,
        description="The assistant's response text.",
    )
    role: Literal["assistant"] = Field(
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
        exclude=True,
        repr=False,
    )


class ChatStreamChunk(BaseModel):
    """A single chunk from a streaming chat completion.

    Streamed one-at-a-time via AsyncIterator[ChatStreamChunk] from the
    provider's stream_generate() method.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

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
    usage: Usage | None = Field(
        default=None,
        description="Cumulative token usage when reported by a streaming provider.",
    )
    raw_chunk: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-native chunk payload (excluded from API responses).",
        exclude=True,
        repr=False,
    )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class EmbedResponse(BaseModel):
    """An embedding response containing one or more embedding vectors."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    embeddings: list[list[float]] = Field(
        ...,
        min_length=1,
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

    @model_validator(mode="after")
    def validate_vector_dimensions(self) -> EmbedResponse:
        """Require non-empty vectors with one stable dimensionality."""
        dimensions = {len(vector) for vector in self.embeddings}
        if 0 in dimensions:
            raise ValueError("embedding vectors must not be empty")
        if len(dimensions) != 1:
            raise ValueError("all embedding vectors must have the same dimensions")
        return self


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------


class RerankResult(BaseModel):
    """A single ranked document with its relevance score."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

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

    model_config = ConfigDict(extra="forbid", frozen=True)

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
