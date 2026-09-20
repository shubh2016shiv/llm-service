"""API dependency contract tests for inference route resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.api.inference_dependencies import require_chat_route
from app.api.shared_dependencies import get_inference_route_resolver
from app.schemas.auth_schema import InferenceAccessContext
from app.schemas.enums import OperationType
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_ID,
    DEPLOYMENT_KEY,
    ENTITLEMENT_ID,
    TENANT_ID,
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
    from app.inference_routing.models import ResolutionRequest, ResolvedRoute
    from app.inference_routing.route_resolution import InferenceRouteResolver

PROVIDER_ID = UUID("40000000-0000-0000-0000-000000000001")
MODEL_ID = UUID("50000000-0000-0000-0000-000000000001")


class RecordingRouteResolver:
    """Record the request passed by the API dependency."""

    def __init__(self, route: ResolvedRoute) -> None:
        self.route = route
        self.requests: list[ResolutionRequest] = []

    async def resolve_route(self, request: ResolutionRequest) -> ResolvedRoute:
        """Record the request and return the configured route."""
        self.requests.append(request)
        return self.route


def build_access_context() -> InferenceAccessContext:
    """Build the authorization result consumed by routing."""
    return InferenceAccessContext(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        deployment_key=DEPLOYMENT_KEY,
        deployment_id=DEPLOYMENT_ID,
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        tenant_role="developer",
        entitlement_id=ENTITLEMENT_ID,
    )


def build_request(app: FastAPI) -> Request:
    """Build a Starlette request carrying application state."""
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_require_chat_route_builds_authorized_resolution_request() -> None:
    """The API passes authorization identity and operation into routing."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )
    route = await build_route_resolver(reader).resolve_route(build_resolution_request())
    resolver = RecordingRouteResolver(route)

    result = await require_chat_route(
        build_access_context(),
        cast("InferenceRouteResolver", resolver),
    )

    request = resolver.requests[0]
    assert result is route
    assert request.operation is OperationType.CHAT
    assert request.tenant_id == TENANT_ID
    assert request.user_id == USER_ID
    assert request.entitlement_id == ENTITLEMENT_ID


def test_get_inference_route_resolver_returns_application_singleton() -> None:
    """The dependency returns the resolver created during startup."""
    app = FastAPI()
    resolver = build_route_resolver(FakeInferenceRoutingConfigReader(None))
    app.state.inference_route_resolver = resolver

    result = get_inference_route_resolver(build_request(app))

    assert result is resolver


def test_get_inference_route_resolver_without_startup_state_raises_runtime_error() -> None:
    """Missing lifespan wiring fails with an actionable error."""
    app = FastAPI()

    with pytest.raises(RuntimeError, match="inference_route_resolver"):
        get_inference_route_resolver(build_request(app))
