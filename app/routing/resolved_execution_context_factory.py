"""
Resolved Execution Context Factory
==================================

Builds the final immutable execution context returned by request resolution.

Architecture:
-------------
    request_resolution_service.py
        │
        ├── credential_resolution_service.py
        ├── provider_route_validation_service.py
        └── resolved_execution_context_factory.py
                │
                └── ResolvedExecutionContext

Dependencies:
    - app.core.settings.models.* — tenant, deployment, entitlement, provider, and model models
    - app.routing.resolution_models — final context contract

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from app.routing.resolution_models import (
    ResolutionSource,
    ResolvedExecutionContext,
)

if TYPE_CHECKING:
    from app.core.settings.models.model_config import LLMModelSpec
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import (
        DeploymentConfig,
        TenantConfig,
        UserEntitlementConfig,
    )
    from app.routing.credential_resolution_service import CredentialSelection


class ResolvedExecutionContextFactory:
    """Builds the final immutable context used by downstream execution layers."""

    def build_for_deployment(
        self,
        *,
        tenant_config: TenantConfig,
        deployment_config: DeploymentConfig,
        provider_static_config: ProviderStaticConfig,
        model_spec: LLMModelSpec,
        credential_selection: CredentialSelection,
    ) -> ResolvedExecutionContext:
        """Build a resolved context from a tenant deployment route."""
        effective_max_tokens = (
            deployment_config.default_max_tokens or model_spec.max_output_tokens
        )
        route_fingerprint = self._compute_route_fingerprint(
            resolution_source=ResolutionSource.TENANT_DEPLOYMENT,
            tenant_id=str(tenant_config.tenant_id),
            provider_name=deployment_config.provider_name,
            model_name=deployment_config.model_name,
            api_endpoint_url=credential_selection.api_endpoint_url,
            cloud_region=credential_selection.cloud_region,
            credential_scope=credential_selection.credential_scope.value,
            secret_reference=credential_selection.secret_reference,
            deployment_key=deployment_config.deployment_key,
        )

        return ResolvedExecutionContext(
            resolution_source=ResolutionSource.TENANT_DEPLOYMENT,
            tenant_config=tenant_config,
            deployment_config=deployment_config,
            user_entitlement_config=None,
            provider_static_config=provider_static_config,
            model_spec=model_spec,
            provider_name=deployment_config.provider_name,
            model_name=deployment_config.model_name,
            api_endpoint_url=credential_selection.api_endpoint_url,
            cloud_region=credential_selection.cloud_region,
            secret_reference=credential_selection.secret_reference,
            credential_scope=credential_selection.credential_scope,
            effective_timeout_seconds=(
                deployment_config.timeout_seconds
                or provider_static_config.default_timeout_seconds
            ),
            effective_max_retries=(
                deployment_config.max_retries
                if deployment_config.max_retries is not None
                else provider_static_config.default_max_retries
            ),
            effective_temperature=deployment_config.default_temperature,
            effective_max_tokens=effective_max_tokens,
            route_fingerprint=route_fingerprint,
        )

    def build_for_user_entitlement(
        self,
        *,
        tenant_config: TenantConfig,
        user_entitlement_config: UserEntitlementConfig,
        provider_static_config: ProviderStaticConfig,
        model_spec: LLMModelSpec,
        credential_selection: CredentialSelection,
    ) -> ResolvedExecutionContext:
        """Build a resolved context from a user-scoped entitlement route."""
        route_fingerprint = self._compute_route_fingerprint(
            resolution_source=ResolutionSource.USER_ENTITLEMENT,
            tenant_id=str(tenant_config.tenant_id),
            provider_name=user_entitlement_config.provider_name,
            model_name=user_entitlement_config.model_name,
            api_endpoint_url=credential_selection.api_endpoint_url,
            cloud_region=credential_selection.cloud_region,
            credential_scope=credential_selection.credential_scope.value,
            secret_reference=credential_selection.secret_reference,
            entitlement_id=str(user_entitlement_config.entitlement_id),
        )

        return ResolvedExecutionContext(
            resolution_source=ResolutionSource.USER_ENTITLEMENT,
            tenant_config=tenant_config,
            deployment_config=None,
            user_entitlement_config=user_entitlement_config,
            provider_static_config=provider_static_config,
            model_spec=model_spec,
            provider_name=user_entitlement_config.provider_name,
            model_name=user_entitlement_config.model_name,
            api_endpoint_url=credential_selection.api_endpoint_url,
            cloud_region=credential_selection.cloud_region,
            secret_reference=credential_selection.secret_reference,
            credential_scope=credential_selection.credential_scope,
            effective_timeout_seconds=provider_static_config.default_timeout_seconds,
            effective_max_retries=provider_static_config.default_max_retries,
            effective_temperature=provider_static_config.default_temperature,
            effective_max_tokens=model_spec.max_output_tokens,
            route_fingerprint=route_fingerprint,
        )

    @staticmethod
    def _compute_route_fingerprint(**payload: str | None) -> str:
        """Compute a stable, opaque digest for the resolved route."""
        normalized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
