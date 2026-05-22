"""
Inference Routing Pipeline
==========================

Single orchestration entry point for inference route resolution.

Flow (in order):
    1) resolve tenant and status
    2) try user entitlement override
    3) fall back to tenant deployment
    4) validate provider/model capability
    5) select credential reference (without reading secret plaintext)
    6) build immutable execution context

Enterprise Pattern: Pipeline Orchestration Pattern
    Each step has one clear responsibility and can be tested independently.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.inference_routing.context_factory import ResolvedExecutionContextFactory
    from app.inference_routing.credential_resolver import CredentialResolver
    from app.inference_routing.deployment_resolver import DeploymentResolver
    from app.inference_routing.entitlement_resolver import UserEntitlementResolver
    from app.inference_routing.models import ResolutionRequest, ResolvedExecutionContext
    from app.inference_routing.provider_validator import ProviderRouteValidator
    from app.inference_routing.tenant_resolver import TenantResolver


class OrchestrationPipeline:
    """Applies precedence rules and returns a single services-ready execution context."""

    def __init__(
        self,
        tenant_resolver: TenantResolver,
        entitlement_resolver: UserEntitlementResolver,
        deployment_resolver: DeploymentResolver,
        provider_validator: ProviderRouteValidator,
        credential_resolver: CredentialResolver,
        context_factory: ResolvedExecutionContextFactory,
    ) -> None:
        self._tenant_resolver = tenant_resolver
        self._entitlement_resolver = entitlement_resolver
        self._deployment_resolver = deployment_resolver
        self._provider_validator = provider_validator
        self._credential_resolver = credential_resolver
        self._context_factory = context_factory

    async def resolve(
        self,
        request: ResolutionRequest,
    ) -> ResolvedExecutionContext:
        """Resolve tenant, route, provider, model, and credential precedence."""
        tenant_config = await self._tenant_resolver.resolve_tenant(request.tenant_id)

        user_entitlement = await self._entitlement_resolver.resolve_override(
            tenant_config=tenant_config,
            request=request,
        )
        if user_entitlement is not None:
            provider_static_config, model_spec = (
                self._provider_validator.resolve_provider_and_model(
                    provider_name=user_entitlement.provider_name,
                    model_name=user_entitlement.model_name,
                    operation=request.operation,
                )
            )
            credential_selection = self._credential_resolver.from_user_entitlement(
                user_entitlement
            )
            return self._context_factory.build_for_user_entitlement(
                tenant_config=tenant_config,
                user_entitlement_config=user_entitlement,
                provider_static_config=provider_static_config,
                model_spec=model_spec,
                credential_selection=credential_selection,
            )

        deployment = await self._deployment_resolver.resolve(
            tenant_id=request.tenant_id,
            deployment_key=request.deployment_key,
        )
        self._tenant_resolver.ensure_provider_allowed(tenant_config, deployment.provider_name)
        provider_static_config, model_spec = (
            self._provider_validator.resolve_provider_and_model(
                provider_name=deployment.provider_name,
                model_name=deployment.model_name,
                operation=request.operation,
            )
        )
        credential_selection = self._credential_resolver.from_deployment(deployment)
        return self._context_factory.build_for_deployment(
            tenant_config=tenant_config,
            deployment_config=deployment,
            provider_static_config=provider_static_config,
            model_spec=model_spec,
            credential_selection=credential_selection,
        )

