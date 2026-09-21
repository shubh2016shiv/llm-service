"""Execution-service regression test for the slim route contract."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

from app.schemas.requests_schema import ChatMessage, ChatRequest
from app.schemas.responses_schema import ChatResponse, ChatStreamChunk, Usage
from app.services.inference import InferenceService
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter
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
    from collections.abc import AsyncIterator

    from app.clients.token_manager_client import (
        FinalizationStatus,
        TokenManagerClient,
        TokenReservation,
    )
    from app.inference_routing.models import ResolvedRoute
    from app.providers.registry import ProviderRegistry

THREAD_ID = UUID("70000000-0000-0000-0000-000000000001")


class RecordingTokenManager:
    """Record quota and usage calls made by inference execution."""

    def __init__(self) -> None:
        self.acquire_calls: list[tuple[object, ResolvedRoute, ChatRequest]] = []
        self.finalize_calls: list[tuple[FinalizationStatus, int | None, int | None]] = []

    async def acquire_reservation(
        self,
        *,
        user_id: object,
        context: ResolvedRoute,
        request: ChatRequest,
        request_id: str | None = None,
    ) -> TokenReservation:
        """Record reservation input and return an acquired reservation."""
        from app.clients.token_manager_client import TokenReservation

        self.acquire_calls.append((user_id, context, request))
        return TokenReservation(
            reservation_id="req-1",
            request_id="correlation-1",
            user_id=USER_ID,
            tenant_id=context.tenant_id,
            token_count=10,
            api_endpoint_url=context.api_endpoint_url,
            expires_at=None,
        )

    async def finalize_reservation(
        self,
        reservation: TokenReservation,
        *,
        status: FinalizationStatus,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """Record the terminal outcome and provider-reported token counts."""
        self.finalize_calls.append((status, prompt_tokens, completion_tokens))


class FakeChatProvider:
    """Return one normalized chat response."""

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Return a response carrying usage for reconciliation."""
        return ChatResponse(
            content="hello",
            model="gpt-4o",
            usage=Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Yield content followed by a provider usage trailer."""
        yield ChatStreamChunk(content="hel")
        yield ChatStreamChunk(
            content="lo",
            finish_reason="stop",
            usage=Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )


class FakeProviderRegistry:
    """Return the configured provider and record the received route."""

    def __init__(self) -> None:
        self.routes: list[ResolvedRoute] = []

    async def get_provider(self, route: ResolvedRoute) -> FakeChatProvider:
        """Record the route selected by inference execution."""
        self.routes.append(route)
        return FakeChatProvider()


class CancellingProviderRegistry:
    """Simulate task cancellation after quota has been reserved."""

    async def get_provider(self, route: ResolvedRoute) -> FakeChatProvider:
        raise asyncio.CancelledError


class FailingFinalizationTokenManager(RecordingTokenManager):
    """Simulate accounting infrastructure failure after provider success."""

    async def finalize_reservation(
        self,
        reservation: TokenReservation,
        *,
        status: FinalizationStatus,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        raise RuntimeError("accounting unavailable")


@pytest.mark.asyncio
async def test_execute_chat_with_slim_route_uses_flat_tenant_identity() -> None:
    """Quota, registry, and usage flows consume the flat slim route fields."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )
    route = await build_route_resolver(reader).resolve_route(build_resolution_request())
    token_manager = RecordingTokenManager()
    registry = FakeProviderRegistry()
    service = InferenceService(
        cast("TokenManagerClient", token_manager),
        cast("ProviderRegistry", registry),
        WorkerStreamCapacityLimiter(max_concurrent=2, retry_after_seconds=1),
    )
    request = ChatRequest(
        thread_id=THREAD_ID,
        messages=[ChatMessage(role="user", content="hello")],
    )

    response = await service.execute_chat(route, request, user_id=USER_ID)

    assert response.content == "hello"
    assert token_manager.acquire_calls == [(USER_ID, route, request)]
    assert token_manager.finalize_calls == [("completed", 3, 2)]
    assert registry.routes == [route]


@pytest.mark.asyncio
async def test_execute_stream_chat_reconciles_usage_after_last_chunk() -> None:
    """Streaming keeps its reservation until the provider stream terminates."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )
    route = await build_route_resolver(reader).resolve_route(build_resolution_request())
    token_manager = RecordingTokenManager()
    registry = FakeProviderRegistry()
    service = InferenceService(
        cast("TokenManagerClient", token_manager),
        cast("ProviderRegistry", registry),
        WorkerStreamCapacityLimiter(max_concurrent=2, retry_after_seconds=1),
    )
    request = ChatRequest(
        thread_id=THREAD_ID,
        messages=[ChatMessage(role="user", content="hello")],
        stream=True,
    )

    stream = await service.prepare_stream_chat(
        route,
        request,
        user_id=USER_ID,
    )
    chunks = [chunk async for chunk in stream]

    assert "".join(chunk.content for chunk in chunks) == "hello"
    assert token_manager.finalize_calls == [("completed", 3, 2)]


@pytest.mark.asyncio
async def test_execute_chat_cancelled_task_finalizes_as_cancelled() -> None:
    """Cancellation is operationally distinct from a provider failure."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )
    route = await build_route_resolver(reader).resolve_route(build_resolution_request())
    token_manager = RecordingTokenManager()
    service = InferenceService(
        cast("TokenManagerClient", token_manager),
        cast("ProviderRegistry", CancellingProviderRegistry()),
        WorkerStreamCapacityLimiter(max_concurrent=2, retry_after_seconds=1),
    )
    request = ChatRequest(
        thread_id=THREAD_ID,
        messages=[ChatMessage(role="user", content="hello")],
    )

    with pytest.raises(asyncio.CancelledError):
        await service.execute_chat(route, request, user_id=USER_ID)

    assert token_manager.finalize_calls == [("cancelled", None, None)]


@pytest.mark.asyncio
async def test_execute_chat_accounting_failure_does_not_report_success() -> None:
    """A completed response is not returned until quota accounting commits."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )
    route = await build_route_resolver(reader).resolve_route(build_resolution_request())
    service = InferenceService(
        cast("TokenManagerClient", FailingFinalizationTokenManager()),
        cast("ProviderRegistry", FakeProviderRegistry()),
        WorkerStreamCapacityLimiter(max_concurrent=2, retry_after_seconds=1),
    )
    request = ChatRequest(
        thread_id=THREAD_ID,
        messages=[ChatMessage(role="user", content="hello")],
    )

    with pytest.raises(RuntimeError, match="accounting unavailable"):
        await service.execute_chat(route, request, user_id=USER_ID)
