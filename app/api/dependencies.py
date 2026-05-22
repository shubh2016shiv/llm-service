"""
Dependency Factories for API Routers.

Architecture:
-------------
    +-----------------------------+
    ¦ API route handler           ¦
    ¦ (Depends declared)          ¦
    +-----------------------------+
                   ?
    +-----------------------------+
    ¦ dependency factory (this)   ¦
    ¦ builds service and helpers  ¦
    +-----------------------------+
                   ?
    +-----------------------------+
    ¦ service layer               ¦
    ¦ business decisions          ¦
    +-----------------------------+
                   ?
    +-----------------------------+
    ¦ persistence/adapters        ¦
    ¦ db and cache integration    ¦
    +-----------------------------+

Purpose:
    Keep route handlers small and declarative. A route says "I need
    TenantService" and this module decides how to assemble it.

Key jargon explained:
    - Dependency injection: FastAPI creates required objects before entering a
      route function and passes them as arguments.
    - Factory: a function that constructs and returns another object.
    - Process-scoped object: created once during app startup and reused across
      requests, usually stored on `app.state`.

Rationale:
    Centralizing construction logic avoids duplication, makes tests easier
    (replace one factory), and keeps lifecycle-sensitive objects (cache,
    orchestration pipeline) in one predictable place.

Author: Shubham Singh
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from app.api.exception_handlers import translate_inference_error
from app.auth import get_current_user
from app.auth.authorization import (
    InferenceAuthorizationCache,
    TenantAccessService,
    TenantAuthorizationService,
)
from app.core.exceptions import LLMServiceError
from app.core.settings.settings import get_application_settings
from app.database import (
    ModelCatalogPersistence,
    ProviderCatalogPersistence,
    TenantDeploymentPersistence,
    TenantMembershipPersistence,
    TenantPersistence,
    UserEntitlementPersistence,
    UserPersistence,
)
from app.inference_routing.models import ResolutionRequest, ResolvedExecutionContext
from app.inference_routing.pipeline import OrchestrationPipeline
from app.schemas.auth_schema import AuthTokenPayload, InferenceAccessContext
from app.schemas.enums import OperationType
from app.services import (
    ManagementReferenceValidationService,
    ModelCatalogService,
    ProviderCatalogService,
    TenantDeploymentService,
    TenantMembershipService,
    TenantService,
    UserEntitlementService,
    UserService,
)

_DEPLOYMENT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
ExecutionContextDependency = Callable[..., Awaitable[ResolvedExecutionContext]]


def get_tenant_access_service() -> TenantAccessService:
    """Create a tenant access service used by management and auth checks.

    Returns:
        TenantAccessService: Service that validates tenant-level access from
            membership data.
    """
    return TenantAccessService(TenantMembershipPersistence())


def get_inference_authorization_cache(request: Request) -> InferenceAuthorizationCache:
    """Create the inference authorization cache wrapper for one request.

    The wrapper is lightweight. It points to the process-level Redis backend
    from `app.state` and carries cache time-to-live from settings.

    Args:
        request: FastAPI request used to access `request.app.state`.

    Returns:
        InferenceAuthorizationCache: Cache facade for auth decisions.
    """
    settings = get_application_settings()
    return InferenceAuthorizationCache(
        backend=getattr(request.app.state, "redis_cache", None),
        ttl_seconds=settings.inference_authorization_cache_ttl_seconds,
    )


def get_tenant_authorization_service(
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> TenantAuthorizationService:
    """Create the tenant authorization service used by inference routes.

    This service performs multi-step checks: tenant state, membership,
    deployment visibility, and entitlement validation.

    Args:
        authorization_cache: Cache for repeated authorization lookups.

    Returns:
        TenantAuthorizationService: Fully wired authorization service.
    """
    return TenantAuthorizationService(
        tenant_persistence=TenantPersistence(),
        membership_persistence=TenantMembershipPersistence(),
        deployment_persistence=TenantDeploymentPersistence(),
        entitlement_persistence=UserEntitlementPersistence(),
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
        TenantAuthorizationService, Depends(get_tenant_authorization_service)
    ],
) -> InferenceAccessContext:
    """Authorize one inference call against tenant and deployment scope.

    Step-by-step:
    1. Parse required tenant and deployment headers.
    2. Read authenticated caller claims.
    3. Validate caller rights for the exact deployment.
    4. Return a compact `InferenceAccessContext` for downstream use.

    Args:
        x_tenant_id: Tenant identifier provided by the caller.
        x_deployment_key: Deployment key selected by the caller.
        current_user: Authenticated user claims.
        authorization_service: Service enforcing inference access policy.

    Returns:
        InferenceAccessContext: Authorized scope for this request.

    Raises:
        HTTPException: Raised indirectly after domain errors are translated.
    """
    try:
        return await authorization_service.authorize_inference(
            tenant_id=x_tenant_id,
            deployment_key=x_deployment_key,
            current_user=current_user,
        )
    except LLMServiceError as exc:
        translate_inference_error(exc)


def get_provider_catalog_service() -> ProviderCatalogService:
    """Create provider catalog service for provider management routes."""
    return ProviderCatalogService(ProviderCatalogPersistence())


def get_model_catalog_service() -> ModelCatalogService:
    """Create model catalog service for provider model routes."""
    return ModelCatalogService(ModelCatalogPersistence())


def get_management_reference_validation_service() -> ManagementReferenceValidationService:
    """Create shared reference validator for management write operations.

    Management writes frequently reference other resources such as users,
    tenants, providers, and models. Centralizing those checks keeps error
    behavior consistent across all management services.
    """
    return ManagementReferenceValidationService(
        tenant_persistence=TenantPersistence(),
        user_persistence=UserPersistence(),
        provider_persistence=ProviderCatalogPersistence(),
        model_persistence=ModelCatalogPersistence(),
    )


def get_tenant_service(
    access_service: Annotated[TenantAccessService, Depends(get_tenant_access_service)],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> TenantService:
    """Create tenant service with access checks and cache coordination."""
    return TenantService(TenantPersistence(), access_service, authorization_cache)


def get_tenant_membership_service(
    access_service: Annotated[TenantAccessService, Depends(get_tenant_access_service)],
    reference_validation_service: Annotated[
        ManagementReferenceValidationService,
        Depends(get_management_reference_validation_service),
    ],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> TenantMembershipService:
    """Create tenant membership service with validation and cache invalidation.

    Membership updates change who can access deployments. This service is wired
    with authorization cache support so stale access entries can be invalidated.
    """
    return TenantMembershipService(
        TenantMembershipPersistence(),
        access_service,
        reference_validation_service,
        authorization_cache,
    )


def get_tenant_deployment_service(
    request: Request,
    access_service: Annotated[TenantAccessService, Depends(get_tenant_access_service)],
    reference_validation_service: Annotated[
        ManagementReferenceValidationService,
        Depends(get_management_reference_validation_service),
    ],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> TenantDeploymentService:
    """Create tenant deployment service with optional Redis acceleration.

    Args:
        request: FastAPI request to read shared app cache backend.
        access_service: Enforces tenant-scoped access before write operations.
        reference_validation_service: Validates provider/model references.
        authorization_cache: Coordinates cache invalidation for auth decisions.

    Returns:
        TenantDeploymentService: Service for deployment lifecycle operations.
    """
    cache = getattr(request.app.state, "redis_cache", None)
    return TenantDeploymentService(
        TenantDeploymentPersistence(),
        access_service,
        reference_validation_service,
        cache,
        authorization_cache,
    )


def get_user_service() -> UserService:
    """Create platform user service for user CRUD endpoints."""
    return UserService(UserPersistence())


def get_user_entitlement_service(
    access_service: Annotated[TenantAccessService, Depends(get_tenant_access_service)],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> UserEntitlementService:
    """Create user entitlement service with access checks and cache support.

    Entitlements define deployment access for a user. Wiring cache here ensures
    authorization cache invalidation can happen when entitlements change.
    """
    return UserEntitlementService(UserEntitlementPersistence(), access_service, authorization_cache)


# ---------------------------------------------------------------------------
# Inference routing pipeline
# ---------------------------------------------------------------------------


def get_orchestration_pipeline(request: Request) -> OrchestrationPipeline:
    """Return the process-scoped orchestration pipeline from `app.state`.

    A process-scoped object is created once during startup and reused across
    requests. This avoids rebuilding expensive routing components per request.

    Raises:
        RuntimeError: If `app.state.orchestration_pipeline` is missing.
    """
    pipeline: OrchestrationPipeline | None = getattr(
        request.app.state, "orchestration_pipeline", None
    )
    if pipeline is None:
        raise RuntimeError(
            "app.state.orchestration_pipeline is not initialised. "
            "Ensure the lifespan handler in main.py creates and stores "
            "an OrchestrationPipeline instance before the application accepts traffic."
        )
    return pipeline


def _make_require_execution_context(
    operation: OperationType,
) -> ExecutionContextDependency:
    """Create a dependency that resolves context for one operation type.

    Chat, embed, and rerank follow the same resolution flow, but each requires
    capability checks for a different operation. This factory avoids copy-paste
    while preserving operation-specific validation.
    """

    async def require_execution_context(
        inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
        pipeline: Annotated[OrchestrationPipeline, Depends(get_orchestration_pipeline)],
    ) -> ResolvedExecutionContext:
        """Resolve provider/model execution context from authorized access scope.

        Args:
            inference_context: Pre-authorized tenant, user, and deployment scope.
            pipeline: Orchestration pipeline that resolves model and provider.

        Returns:
            ResolvedExecutionContext: Full runtime context consumed by
                `InferenceService` execution methods.

        Raises:
            HTTPException: Raised indirectly after domain errors are translated.
        """
        try:
            return await pipeline.resolve(
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

    return require_execution_context


require_chat_execution_context = _make_require_execution_context(OperationType.CHAT)
require_embed_execution_context = _make_require_execution_context(OperationType.EMBED)
require_rerank_execution_context = _make_require_execution_context(OperationType.RERANK)

