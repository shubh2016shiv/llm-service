"""Tests for typed application-state dependency access."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request

from app.api.shared_dependencies import require_app_state


def _request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )


def test_require_app_state_rejects_wrong_runtime_type() -> None:
    """A wiring mistake fails at the dependency boundary with useful context."""
    app = FastAPI()
    app.state.example = "not-an-integer"

    with pytest.raises(RuntimeError, match="has type str; expected int"):
        require_app_state(_request(app), "example", int, hint="Initialize example.")


def test_require_app_state_returns_typed_value() -> None:
    """Correctly wired lifespan state passes through unchanged."""
    app = FastAPI()
    app.state.example = 42

    assert require_app_state(_request(app), "example", int, hint="Initialize example.") == 42
