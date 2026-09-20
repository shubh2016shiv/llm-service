"""Contract tests for the llm_token_manager HTTP client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from jose import jwt

from app.clients.token_manager_client import (
    TokenManagerClient,
    TokenManagerProtocolError,
    TokenReservationRejectedError,
)
from app.schemas.requests_schema import ChatMessage, ChatRequest
from tests.unit.inference_routing.conftest import (
    USER_ID,
    build_tenant_config,
    build_user_entitlement_config,
)
from tests.unit.inference_routing.routing_fakes import (
    FakeInferenceRoutingConfigReader,
    build_resolution_request,
    build_route_resolver,
)

if TYPE_CHECKING:
    from app.inference_routing.models import ResolvedRoute

SECRET = "a-production-length-test-secret-that-is-at-least-32-bytes"


async def _route() -> ResolvedRoute:
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )
    return await build_route_resolver(reader).resolve_route(build_resolution_request())


def _client(handler: httpx.AsyncBaseTransport) -> TokenManagerClient:
    http_client = httpx.AsyncClient(transport=handler, base_url="http://token-manager")
    return TokenManagerClient(
        base_url="http://unused",
        service_id="llm-services",
        jwt_secret_key=SECRET,
        jwt_algorithm="HS256",
        timeout=httpx.Timeout(5),
        http_client=http_client,
    )


@pytest.mark.asyncio
async def test_acquire_and_finalize_follow_token_manager_contract() -> None:
    """Client sends tenant-scoped auth and actual usage on finalization."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/acquire"):
            return httpx.Response(
                201,
                json={
                    "token_request_id": "reservation-1",
                    "allocation_status": "ACQUIRED",
                    "token_count": 17,
                    "api_endpoint_url": "https://api.openai.com/v1",
                    "expires_at": "2026-09-19T10:00:00Z",
                },
            )
        return httpx.Response(200, json={"allocation_status": "RELEASED"})

    client = _client(httpx.MockTransport(handle))
    route = await _route()
    request = ChatRequest(messages=[ChatMessage(role="user", content="hello")])

    reservation = await client.acquire_reservation(
        user_id=USER_ID,
        context=route,
        request=request,
        request_id="correlation-1",
    )
    await client.finalize_reservation(
        reservation,
        status="completed",
        prompt_tokens=3,
        completion_tokens=2,
    )

    acquire_claims = jwt.decode(
        requests[0].headers["Authorization"].removeprefix("Bearer "),
        SECRET,
        algorithms=["HS256"],
    )
    assert acquire_claims["tenant_id"] == str(route.tenant_id)
    assert requests[0].headers["X-Service-ID"] == "llm-services"
    assert requests[0].read().decode().find('"deployment_name":"gpt4-production"') >= 0
    release_claims = jwt.decode(
        requests[1].headers["Authorization"].removeprefix("Bearer "),
        SECRET,
        algorithms=["HS256"],
    )
    assert release_claims["user_id"] == str(USER_ID)
    assert release_claims["tenant_id"] == str(route.tenant_id)
    assert requests[1].read().decode().find('"actual_completion_tokens":2') >= 0


@pytest.mark.asyncio
async def test_waiting_allocation_is_rejected_with_retry_hint() -> None:
    """WAITING is not executable and becomes a typed quota rejection."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            headers={"Retry-After": "7"},
            json={"allocation_status": "WAITING"},
        )

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(TokenReservationRejectedError) as exc_info:
        await client.acquire_reservation(
            user_id=USER_ID,
            context=await _route(),
            request=ChatRequest(messages=[ChatMessage(role="user", content="hello")]),
        )

    assert exc_info.value.retry_after_seconds == 7


@pytest.mark.asyncio
async def test_reserved_endpoint_must_match_authorized_route() -> None:
    """A reservation cannot redirect execution to a different endpoint."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "token_request_id": "reservation-1",
                "allocation_status": "ACQUIRED",
                "token_count": 17,
                "api_endpoint_url": "https://unexpected.example/v1",
            },
        )

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(TokenManagerProtocolError, match="different deployment"):
        await client.acquire_reservation(
            user_id=USER_ID,
            context=await _route(),
            request=ChatRequest(messages=[ChatMessage(role="user", content="hello")]),
        )
