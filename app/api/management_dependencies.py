"""Service-composition dependencies used by management API routers.

Routers declare the service they need; these factories wire persistence,
authorization, cache invalidation, and credential writing. No business rule
lives here—the module is solely the composition boundary.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.shared_dependencies import (
    AuthorizationCacheDependency,
    CredentialWriterDependency,
    PostgresSessionProviderDependency,
    TenantAccessServiceDependency,
    get_config_loader,
)
from app.core.settings.loader import ConfigLoader
from app.database import (
    ModelCatalogPersistence,
    ProviderCatalogPersistence,
    TenantDeploymentPersistence,
    TenantMembershipPersistence,
    TenantPersistence,
    UserEntitlementPersistence,
    UserPersistence,
)
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


def get_provider_catalog_service(
    session_provider: PostgresSessionProviderDependency,
    config_loader: Annotated[ConfigLoader, Depends(get_config_loader)],
) -> ProviderCatalogService:
    """Build provider-catalog operations over PostgreSQL."""
    return ProviderCatalogService(ProviderCatalogPersistence(session_provider), config_loader)


def get_model_catalog_service(
    session_provider: PostgresSessionProviderDependency,
    config_loader: Annotated[ConfigLoader, Depends(get_config_loader)],
) -> ModelCatalogService:
    """Build model-catalog operations over PostgreSQL."""
    return ModelCatalogService(
        ModelCatalogPersistence(session_provider),
        ProviderCatalogPersistence(session_provider),
        config_loader,
    )


def get_management_reference_validation_service(
    session_provider: PostgresSessionProviderDependency,
) -> ManagementReferenceValidationService:
    """Build shared foreign-reference validation for management writes."""
    return ManagementReferenceValidationService(
        tenant_persistence=TenantPersistence(session_provider),
        user_persistence=UserPersistence(session_provider),
        provider_persistence=ProviderCatalogPersistence(session_provider),
        model_persistence=ModelCatalogPersistence(session_provider),
    )


ReferenceValidationDependency = Annotated[
    ManagementReferenceValidationService,
    Depends(get_management_reference_validation_service),
]


def get_tenant_service(
    session_provider: PostgresSessionProviderDependency,
    access_service: TenantAccessServiceDependency,
    authorization_cache: AuthorizationCacheDependency,
) -> TenantService:
    """Build tenant lifecycle operations with access-cache invalidation."""
    return TenantService(
        TenantPersistence(session_provider),
        access_service,
        authorization_cache,
    )


def get_tenant_membership_service(
    session_provider: PostgresSessionProviderDependency,
    access_service: TenantAccessServiceDependency,
    reference_validation_service: ReferenceValidationDependency,
    authorization_cache: AuthorizationCacheDependency,
) -> TenantMembershipService:
    """Build membership operations and authorization-cache coordination."""
    return TenantMembershipService(
        TenantMembershipPersistence(session_provider),
        access_service,
        reference_validation_service,
        authorization_cache,
    )


def get_tenant_deployment_service(
    session_provider: PostgresSessionProviderDependency,
    access_service: TenantAccessServiceDependency,
    reference_validation_service: ReferenceValidationDependency,
    authorization_cache: AuthorizationCacheDependency,
    credential_writer: CredentialWriterDependency,
) -> TenantDeploymentService:
    """Build deployment operations with grant invalidation and secret writing."""
    return TenantDeploymentService(
        deployment_persistence=TenantDeploymentPersistence(session_provider),
        access_service=access_service,
        reference_validation_service=reference_validation_service,
        credential_writer=credential_writer,
        authorization_cache=authorization_cache,
    )


def get_user_service(
    session_provider: PostgresSessionProviderDependency,
) -> UserService:
    """Build platform-user CRUD operations."""
    return UserService(UserPersistence(session_provider))


def get_user_entitlement_service(
    session_provider: PostgresSessionProviderDependency,
    access_service: TenantAccessServiceDependency,
    authorization_cache: AuthorizationCacheDependency,
    credential_writer: CredentialWriterDependency,
) -> UserEntitlementService:
    """Build entitlement operations with credentials and cache invalidation."""
    return UserEntitlementService(
        UserEntitlementPersistence(session_provider),
        access_service,
        TenantDeploymentPersistence(session_provider),
        credential_writer,
        authorization_cache=authorization_cache,
    )
