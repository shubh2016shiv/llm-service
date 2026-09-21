"""Provider-neutral contracts for text and structured streaming output.

Architecture:
    any producer -> StreamEventPayload -> SSEStreamDelivery -> client

Producers describe semantic events; transports add delivery metadata. This
keeps thread identity, sequence numbers, and SSE framing out of provider code
and lets the same contract carry text deltas, structured patches, progress, or
tool events.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

StreamEventName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    ),
]
JsonPointer = Annotated[str, StringConstraints(max_length=1024)]


class StreamEventPayload(BaseModel):
    """One semantic event produced before transport metadata is attached."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    event_name: StreamEventName
    data: JsonValue


class StreamEventEnvelope(BaseModel):
    """Public event body shared by every SSE data event in one thread."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    thread_id: UUID
    sequence: int = Field(ge=1)
    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    data: JsonValue


class StructuredOutputDelta(BaseModel):
    """A JSON-Pointer update for incrementally assembled structured output.

    ``path`` follows JSON Pointer notation: ``""`` targets the root and
    ``"/customer/name"`` targets a nested field. ``append`` adds an item to a
    list at the selected path. This transports already parsed structure; a
    provider-specific incremental JSON parser remains an adapter concern.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    operation: Literal["add", "replace", "remove", "append"]
    path: JsonPointer = ""
    value: JsonValue = None

    @model_validator(mode="after")
    def validate_json_pointer(self) -> StructuredOutputDelta:
        """Require the empty root pointer or an absolute JSON Pointer path."""
        if self.path and not self.path.startswith("/"):
            raise ValueError("path must be empty or start with '/' per JSON Pointer")
        return self


def text_delta_event(text: str, *, index: int = 0) -> StreamEventPayload:
    """Create a portable text-delta event from generated text."""
    return StreamEventPayload(
        event_name="text_delta",
        data={"text": text, "index": index},
    )


def structured_delta_event(delta: StructuredOutputDelta) -> StreamEventPayload:
    """Create a portable structured-output event from one parsed update."""
    return StreamEventPayload(
        event_name="structured_delta",
        data=delta.model_dump(mode="json"),
    )
