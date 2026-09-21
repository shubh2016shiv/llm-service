"""
Request Schemas
===============

Inbound request models for chat, embeddings, and rerank operations.

Enterprise Pattern: Boundary Validation Pattern
    Inputs are validated once at the API boundary using strict typed schemas.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# These limits complement the HTTP body-size middleware. The middleware caps
# bytes on the wire; these constraints cap semantic fan-out (for example, one
# request creating thousands of provider calls or ranking candidates).
PromptText = Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
StopSequence = Annotated[str, StringConstraints(min_length=1, max_length=256)]
ParticipantName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
]
EmbeddingBatch = Annotated[list[PromptText], Field(min_length=1, max_length=256)]

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single message in a chat conversation.

    Mirrors the OpenAI message schema: {role, content, name?}.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"] = Field(
        ...,
        description="Message author role.",
    )
    content: PromptText = Field(
        ...,
        description=(
            "Text content sent to the provider. Tool calls are not part of this API contract, "
            "so an empty message is rejected rather than relying on untyped extra fields."
        ),
    )
    name: ParticipantName | None = Field(
        default=None,
        description="Optional participant name (function name / username).",
    )


class ChatRequest(BaseModel):
    """A chat completion request dispatched to a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    messages: list[ChatMessage] = Field(
        ...,
        min_length=1,
        max_length=128,
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
    stop: list[StopSequence] | None = Field(
        default=None,
        min_length=1,
        max_length=16,
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

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: PromptText | EmbeddingBatch = Field(
        ...,
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

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: PromptText = Field(
        ...,
        description="Search query to rank documents against.",
    )
    documents: list[PromptText] = Field(
        ...,
        min_length=1,
        max_length=1_000,
        description="Candidate documents to re-rank.",
    )
    top_n: int | None = Field(
        default=None,
        ge=1,
        description="Return only the top-N documents. Returns all when None.",
    )

    @model_validator(mode="after")
    def validate_top_n_fits_documents(self) -> RerankRequest:
        """Reject a result count larger than the candidate set."""
        if self.top_n is not None and self.top_n > len(self.documents):
            raise ValueError("top_n cannot exceed the number of documents")
        return self
