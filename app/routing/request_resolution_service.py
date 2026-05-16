"""
Request Resolution Service
==========================

Applies the full precedence chain for tenant-aware LLM route resolution.

Architecture:
-------------
    caller
        │
        ▼
    RequestResolutionService.resolve()
        │
        ├── TenantResolutionService
        ├── UserEntitlementResolutionService
        ├── DeploymentResolutionService
        ├── ProviderRouteValidationService
        ├── CredentialResolutionService
        └── ResolvedExecutionContextFactory

Dependencies:
    - app.routing.resolution_models — request and result contracts
    - app.routing.* — focused resolution sub-services

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from app.routing.credential_resolution_service import (
    CredentialResolutionService,
)
from app.routing.deployment_resolution_service import (
    DeploymentResolutionService,
)
from app.routing.provider_route_validation_service import (
    ProviderRouteValidationService,
)
from app.routing.resolution_models import (
    ResolvedExecutionContext,
    ResolutionRequest,
)
from app.routing.resolved_execution_context_factory import (
    ResolvedExecutionContextFactory,
)
from app.routing.tenant_resolution_service import (
    TenantResolutionService,
)
from app.routing.user_entitlement_resolution_service import (
    UserEntitlementResolutionService,
)


class RequestResolutionService:
    """Applies precedence rules and returns a single execution-ready context."""

    def __init__(
        self,
        tenant_resolution_service: TenantResolutionService,
        user_entitlement_resolution_service: UserEntitlementResolutionService,
        deployment_resolution_service: DeploymentResolutionService,
        provider_route_validation_service: ProviderRouteValidationService,
        credential_resolution_service: CredentialResolutionService,
        resolved_execution_context_factory: ResolvedExecutionContextFactory,
    ) -> None:
        self._tenant_resolution_service = tenant_resolution_service
        self._user_entitlement_resolution_service = user_entitlement_resolution_service
        self._deployment_resolution_service = deployment_resolution_service
        self._provider_route_validation_service = provider_route_validation_service
        self._credential_resolution_service = credential_resolution_service
        self._resolved_execution_context_factory = resolved_execution_context_factory

    async def resolve(
        self,
        request: ResolutionRequest,
    ) -> ResolvedExecutionContext:
        """Resolve tenant, route, provider, model, and credential precedence."""
        tenant_config = await self._tenant_resolution_service.resolve_tenant(
            request.tenant_id
        )

        user_entitlement = await self._user_entitlement_resolution_service.resolve_override(
            tenant_config=tenant_config,
            request=request,
        )
        if user_entitlement is not None:
            provider_static_config, model_spec = (
                self._provider_route_validation_service.resolve_provider_and_model(
                    provider_name=user_entitlement.provider_name,
                    model_name=user_entitlement.model_name,
                    operation=request.operation,
                )
            )
            credential_selection = (
                self._credential_resolution_service.from_user_entitlement(
                    user_entitlement
                )
            )
            return self._resolved_execution_context_factory.build_for_user_entitlement(
                tenant_config=tenant_config,
                user_entitlement_config=user_entitlement,
                provider_static_config=provider_static_config,
                model_spec=model_spec,
                credential_selection=credential_selection,
            )

        deployment = await self._deployment_resolution_service.resolve_deployment(
            tenant_id=request.tenant_id,
            deployment_key=request.deployment_key,
        )
        self._tenant_resolution_service.ensure_provider_allowed(
            tenant_config,
            deployment.provider_name,
        )
        provider_static_config, model_spec = (
            self._provider_route_validation_service.resolve_provider_and_model(
                provider_name=deployment.provider_name,
                model_name=deployment.model_name,
                operation=request.operation,
            )
        )
        credential_selection = self._credential_resolution_service.from_deployment(
            deployment
        )
        return self._resolved_execution_context_factory.build_for_deployment(
            tenant_config=tenant_config,
            deployment_config=deployment,
            provider_static_config=provider_static_config,
            model_spec=model_spec,
            credential_selection=credential_selection,
        )
