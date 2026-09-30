"""Tests for inference header validation at the HTTP dependency boundary."""

from __future__ import annotations

from typing import Annotated
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.inference_dependencies import (
    get_inference_authorization_service,
    require_inference_access,
)
from app.auth import get_current_user


def _client(authorize: AsyncMock) -> TestClient:
    app = FastAPI()

    @app.get("/protected")
    async def protected(
        _context: Annotated[object, Depends(require_inference_access)],
    ) -> dict[str, bool]:
        return {"authorized": True}

    service = type("AuthorizationService", (), {"authorize_inference": authorize})()
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_inference_authorization_service] = lambda: service
    return TestClient(app)


def test_kebab_case_deployment_header_reaches_authorization() -> None:
    """A storable deployment key passes the HTTP contract unchanged."""
    authorize = AsyncMock(return_value=object())
    client = _client(authorize)

    response = client.get(
        "/protected",
        headers={
            "X-Tenant-ID": "10000000-0000-0000-0000-000000000001",
            "X-Deployment-Key": "gpt-4o-prod",
        },
    )

    assert response.status_code == 200
    authorize.assert_awaited_once()


@pytest.mark.parametrize(
    "deployment_key",
    ["GPT-4o-Prod", "gpt_4o", "gpt.4o", "gpt--4o", "-gpt4o", "gpt4o-"],
)
def test_non_kebab_deployment_header_is_rejected_before_authorization(
    deployment_key: str,
) -> None:
    """Impossible deployment keys produce an actionable 422 at the boundary."""
    authorize = AsyncMock(return_value=object())
    client = _client(authorize)

    response = client.get(
        "/protected",
        headers={
            "X-Tenant-ID": "10000000-0000-0000-0000-000000000001",
            "X-Deployment-Key": deployment_key,
        },
    )

    assert response.status_code == 422
    authorize.assert_not_awaited()
