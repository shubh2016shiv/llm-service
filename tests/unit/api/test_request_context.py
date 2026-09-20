"""Tests for correlation-ID trust-boundary behavior."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.request_context import RequestBodyLimitMiddleware, resolve_request_id


def test_valid_upstream_request_id_is_preserved() -> None:
    """A normal trace ID remains stable across service boundaries."""
    request_id, accepted = resolve_request_id("gateway:01J8-test_123")

    assert request_id == "gateway:01J8-test_123"
    assert accepted is True


@pytest.mark.parametrize(
    "unsafe_value",
    ["contains space", "line\nbreak", "x" * 129, ""],
)
def test_unsafe_request_id_is_replaced(unsafe_value: str) -> None:
    """Control characters and unbounded IDs never reach logs or headers."""
    request_id, accepted = resolve_request_id(unsafe_value)

    assert accepted is False
    assert UUID(request_id).version == 4


def test_declared_oversized_body_is_rejected_before_route_parsing() -> None:
    """The API returns 413 without reflecting any request content."""
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=8)

    @app.post("/payload")
    async def accept_payload() -> dict[str, bool]:
        return {"accepted": True}

    response = TestClient(app).post("/payload", content="sensitive payload")

    assert response.status_code == 413
    assert response.json()["error_code"] == "REQUEST_BODY_TOO_LARGE"
    assert "sensitive payload" not in response.text
