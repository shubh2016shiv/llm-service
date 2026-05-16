"""
Routing Package
===============

Public package surface for tenant-aware route resolution.

Architecture:
-------------
    app.routing
    ├── request_resolution_service.py        → RequestResolutionService
    ├── tenant_resolution_service.py         → TenantResolutionService
    ├── user_entitlement_resolution_service.py → UserEntitlementResolutionService
    ├── deployment_resolution_service.py     → DeploymentResolutionService
    ├── provider_route_validation_service.py → ProviderRouteValidationService
    ├── credential_resolution_service.py     → CredentialResolutionService
    ├── resolved_execution_context_factory.py → ResolvedExecutionContextFactory
    ├── resolution_models.py                 → ResolutionRequest, ResolvedExecutionContext
    └── contracts.py                         → Reader protocols

Author: Engineering Team
Last Updated: 2026-05-16
"""

from app.routing.contracts import (
    TenantConfigReader,
    UserEntitlementReader,
)
from app.routing.credential_resolution_service import (
    CredentialResolutionService,
    CredentialSelection,
)
from app.routing.deployment_resolution_service import (
    DeploymentResolutionService,
)
from app.routing.deployment_resolver import DeploymentResolver
from app.routing.provider_route_validation_service import (
    ProviderRouteValidationService,
)
from app.routing.request_resolution_service import (
    RequestResolutionService,
)
from app.routing.resolution_models import (
    CredentialScope,
    ResolvedExecutionContext,
    ResolutionRequest,
    ResolutionSource,
)
from app.routing.resolved_execution_context_factory import (
    ResolvedExecutionContextFactory,
)
from app.routing.tenant_resolution_service import TenantResolutionService
from app.routing.user_entitlement_resolution_service import (
    UserEntitlementResolutionService,
)

__all__ = [
    "CredentialResolutionService",
    "CredentialScope",
    "CredentialSelection",
    "DeploymentResolutionService",
    "DeploymentResolver",
    "ProviderRouteValidationService",
    "RequestResolutionService",
    "ResolutionRequest",
    "ResolutionSource",
    "ResolvedExecutionContext",
    "ResolvedExecutionContextFactory",
    "TenantConfigReader",
    "TenantResolutionService",
    "UserEntitlementReader",
    "UserEntitlementResolutionService",
]
