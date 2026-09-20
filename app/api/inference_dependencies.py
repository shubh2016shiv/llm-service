"""Authentication and route-resolution dependencies for inference endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header

from app.api.exception_handlers import translate_inference_error
from app.api.shared_dependencies import (
    AuthorizationCacheDependency,
    PostgresSessionProviderDependency,
    get_inference_route_resolver,
)
from app.auth import get_current_user
from app.auth.authorization import InferenceAuthorizationService
from app.core.exceptions import LLMServiceError
from app.database import (
    TenantDeploymentPersistence,
    TenantMembershipPersistence,
    TenantPersistence,
    UserEntitlementPersistence,
)
from app.inference_routing.models import ResolutionRequest, ResolvedRoute
from app.inference_routing.route_resolution import InferenceRouteResolver
from app.schemas.auth_schema import AuthTokenPayload, InferenceAccessContext
from app.schemas.enums import OperationType

_DEPLOYMENT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
RouteDependency = Callable[..., Awaitable[ResolvedRoute]]


def get_inference_authorization_service(
    session_provider: PostgresSessionProviderDependency,
    authorization_cache: AuthorizationCacheDependency,
) -> InferenceAuthorizationService:
    """Compose the four authorization gates and their positive-result cache."""
    return InferenceAuthorizationService(
        tenant_persistence=TenantPersistence(session_provider),
        membership_persistence=TenantMembershipPersistence(session_provider),
        deployment_persistence=TenantDeploymentPersistence(session_provider),
        entitlement_persistence=UserEntitlementPersistence(session_provider),
        authorization_cache=authorization_cache,
    )


async def require_inference_access(
    x_tenant_id: Annotated[UUID, Header(alias="X-Tenant-ID")],
    x_deployment_key: Annotated[
        str,
        Header(
            alias="X-Deployment-Key",
            min_length=1,
            max_length=128,
            pattern=_DEPLOYMENT_KEY_PATTERN,
        ),
    ],
    current_user: Annotated[AuthTokenPayload, Depends(get_current_user)],
    authorization_service: Annotated[
        InferenceAuthorizationService,
        Depends(get_inference_authorization_service),
    ],
) -> InferenceAccessContext:
    """Authorize the caller for one exact tenant/deployment pair."""
    try:
        return await authorization_service.authorize_inference(
            tenant_id=x_tenant_id,
            deployment_key=x_deployment_key,
            current_user=current_user,
        )
    except LLMServiceError as exc:
        translate_inference_error(exc)


def _make_require_route(operation: OperationType) -> RouteDependency:
    """Build one operation-specific route dependency without copy/paste."""

    async def require_route(
        inference_context: Annotated[
            InferenceAccessContext,
            Depends(require_inference_access),
        ],
        route_resolver: Annotated[
            InferenceRouteResolver,
            Depends(get_inference_route_resolver),
        ],
    ) -> ResolvedRoute:
        try:
            return await route_resolver.resolve_route(
                ResolutionRequest(
                    tenant_id=inference_context.tenant_id,
                    user_id=inference_context.user_id,
                    deployment_key=inference_context.deployment_key,
                    operation=operation,
                    pre_authorized_entitlement_id=inference_context.entitlement_id,
                )
            )
        except LLMServiceError as exc:
            translate_inference_error(exc)

    return require_route


require_chat_route = _make_require_route(OperationType.CHAT)
require_embed_route = _make_require_route(OperationType.EMBED)
require_rerank_route = _make_require_route(OperationType.RERANK)
