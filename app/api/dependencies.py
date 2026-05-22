"""
app/api/dependencies.py — FastAPI dependency factories.

Wires persistence, auth, and business-logic objects into FastAPI's Depends()
graph. Every factory here is O(request) — no shared mutable state.

Exception translation lives in app.api.exception_handlers, not here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from app.api.exception_handlers import translate_inference_error
from app.auth import get_current_user
from app.auth.authorization import InferenceAuthorizationCache, TenantAccessService, TenantAuthorizationService
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
from app.schemas.auth_schema import AuthTokenPayload, InferenceAccessContext
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


def get_tenant_access_service() -> TenantAccessService:
    """Build a tenant access service with membership persistence."""
    return TenantAccessService(TenantMembershipPersistence())


def get_inference_authorization_cache(request: Request) -> InferenceAuthorizationCache:
    """Build the Redis-backed inference authorization cache wrapper."""
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
    """Build an inference authorization service with Redis-backed cache."""
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
    """Authorize a caller to invoke one tenant deployment."""
    try:
        return await authorization_service.authorize_inference(
            tenant_id=x_tenant_id,
            deployment_key=x_deployment_key,
            current_user=current_user,
        )
    except LLMServiceError as exc:
        translate_inference_error(exc)


def get_provider_catalog_service() -> ProviderCatalogService:
    """Build provider catalog service."""
    return ProviderCatalogService(ProviderCatalogPersistence())


def get_model_catalog_service() -> ModelCatalogService:
    """Build model catalog service."""
    return ModelCatalogService(ModelCatalogPersistence())


def get_management_reference_validation_service() -> ManagementReferenceValidationService:
    """Build the shared reference validator for management create flows."""
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
    """Build tenant service."""
    return TenantService(TenantPersistence(), access_service, authorization_cache)


def get_tenant_membership_service(
    access_service: Annotated[TenantAccessService, Depends(get_tenant_access_service)],
    reference_validation_service: Annotated[
        ManagementReferenceValidationService, Depends(get_management_reference_validation_service)
    ],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> TenantMembershipService:
    """Build tenant membership service."""
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
        ManagementReferenceValidationService, Depends(get_management_reference_validation_service)
    ],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> TenantDeploymentService:
    """Build tenant deployment service with optional app cache."""
    cache = getattr(request.app.state, "redis_cache", None)
    return TenantDeploymentService(
        TenantDeploymentPersistence(),
        access_service,
        reference_validation_service,
        cache,
        authorization_cache,
    )


def get_user_service() -> UserService:
    """Build user service."""
    return UserService(UserPersistence())


def get_user_entitlement_service(
    access_service: Annotated[TenantAccessService, Depends(get_tenant_access_service)],
    authorization_cache: Annotated[
        InferenceAuthorizationCache, Depends(get_inference_authorization_cache)
    ],
) -> UserEntitlementService:
    """Build user entitlement service."""
    return UserEntitlementService(UserEntitlementPersistence(), access_service, authorization_cache)
