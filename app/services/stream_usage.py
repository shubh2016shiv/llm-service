"""Accumulate cumulative usage snapshots from a provider stream.

Architecture:
    provider chunks -> StreamUsageAccumulator -> StreamingInferenceSession

Usage accounting belongs to the inference use case, not the SSE transport. A
different transport (WebSocket, gRPC, CLI) must reconcile the same reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.responses_schema import Usage


@dataclass(slots=True)
class StreamUsageAccumulator:
    """Retain the largest cumulative token counts observed in provider trailers."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def observe(self, usage: Usage | None) -> None:
        """Merge cumulative snapshots without double-counting repeated totals."""
        if usage is None:
            return
        self.prompt_tokens = max(self.prompt_tokens or 0, usage.prompt_tokens)
        self.completion_tokens = max(
            self.completion_tokens or 0,
            usage.completion_tokens,
        )
