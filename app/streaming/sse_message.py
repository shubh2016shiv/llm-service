"""Represent one standards-compliant Server-Sent Events wire message.

Architecture:
    SSEStreamDelivery -> SSEMessage -> sse_encoder.py -> HTTP response bytes

The model is intentionally independent of FastAPI and LLM schemas, allowing
the protocol encoder to be reused in any asynchronous Python application.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SSEMessage:
    """One SSE message before line-oriented wire serialization."""

    data: str | None = None
    event_name: str | None = None
    event_id: str | None = None
    comment: str | None = None
    retry_milliseconds: int | None = None

    def __post_init__(self) -> None:
        """Prevent field injection and invalid browser retry directives."""
        _require_single_line("event_name", self.event_name)
        _require_single_line("event_id", self.event_id)
        if self.retry_milliseconds is not None and self.retry_milliseconds < 0:
            raise ValueError("retry_milliseconds must not be negative")


def _require_single_line(field_name: str, value: str | None) -> None:
    """Reject CR/LF because they would create additional SSE fields."""
    if value is not None and ("\r" in value or "\n" in value):
        raise ValueError(f"{field_name} must not contain CR or LF characters")
