"""Security-contract tests for structured logging and recursive redaction."""

from __future__ import annotations

import json
import logging
from collections import deque

from app.core.logging import JSONFormatter, RedactFilter


def test_top_level_generic_sequence_is_redacted_like_nested_sequence() -> None:
    """A container's depth must not decide whether a named secret is exposed."""
    # Arrange
    secret = "sk-must-not-reach-output"
    top_level = deque([{"api_key": secret}])
    nested = {"inner": deque([{"api_key": secret}])}
    record = logging.makeLogRecord(
        {
            "name": "test.redaction",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "redaction check",
            "args": (),
            "top_level_deque": top_level,
            "nested": nested,
        }
    )

    # Act
    RedactFilter().filter(record)
    rendered = JSONFormatter().format(record)
    payload = json.loads(rendered)

    # Assert
    assert payload["top_level_deque"] == [{"api_key": "[REDACTED]"}]
    assert payload["nested"] == {"inner": [{"api_key": "[REDACTED]"}]}
    assert secret not in rendered
    # Redaction rebuilds containers; logging must never alter application data.
    assert top_level[0]["api_key"] == secret
    assert nested["inner"][0]["api_key"] == secret


def test_string_and_bytes_values_are_not_treated_as_nested_containers() -> None:
    """Sequence support must not split ordinary scalar text into elements."""
    record = logging.makeLogRecord(
        {
            "name": "test.redaction",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "scalar check",
            "args": (),
            "plain_text": "safe text",
            "binary_value": b"safe bytes",
        }
    )

    RedactFilter().filter(record)

    assert record.plain_text == "safe text"
    assert record.binary_value == b"safe bytes"
