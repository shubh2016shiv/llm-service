"""Serialize SSE messages without framework or application dependencies.

Architecture:
    SSEMessage -> encode_sse_message -> UTF-8 response stream

SSE is a line-oriented protocol. Multi-line data and comments require one
field prefix per line, followed by a blank line that dispatches the message.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .sse_message import SSEMessage

if TYPE_CHECKING:
    from pydantic import JsonValue


def encode_sse_message(message: SSEMessage) -> str:
    """Encode one message using the WHATWG SSE wire format."""
    lines: list[str] = []
    if message.comment is not None:
        lines.extend(_field_lines(": ", message.comment))
    if message.event_id is not None:
        lines.append(f"id: {message.event_id}")
    if message.event_name is not None:
        lines.append(f"event: {message.event_name}")
    if message.retry_milliseconds is not None:
        lines.append(f"retry: {message.retry_milliseconds}")
    if message.data is not None:
        lines.extend(_field_lines("data: ", message.data))
    return "\n".join(lines) + "\n\n"


def encode_sse_json(
    payload: JsonValue,
    *,
    event_name: str,
    event_id: str | None = None,
) -> str:
    """Serialize JSON compactly and place it in one named SSE message."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return encode_sse_message(
        SSEMessage(data=data, event_name=event_name, event_id=event_id)
    )


def _field_lines(prefix: str, value: str) -> list[str]:
    """Apply an SSE field prefix to every logical line, including empty data."""
    return [f"{prefix}{line}" for line in value.splitlines() or [""]]
