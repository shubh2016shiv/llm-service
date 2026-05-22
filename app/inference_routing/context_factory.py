"""
Resolved Execution Context Factory
==================================

Builds the final immutable execution context used by inference services.

Why a factory instead of just constructing the object directly?
    Creating a ``ResolvedExecutionContext`` requires data from multiple
    sources — tenant config, deployment config, provider config, model spec,
    and credential selection. A factory centralizes this assembly so that
    every consumer gets a consistent, fully-built, read-only context. It also
    computes the route fingerprint (a hash used for authorization caching)
    in one place, guaranteeing the same inputs always produce the same
    fingerprint.

Enterprise Pattern: Factory Pattern
    Assembly logic is centralized so downstream services receive one complete,
    typed, read-only context object.

Architecture decision:
    Route fingerprint generation lives in this factory so both precedence paths
    (deployment and entitlement) use one canonical hashing strategy.

Author: Shubham Singh
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from app.inference_routing.models import ResolutionSource, ResolvedExecutionContext

if TYPE_CHECKING:
    from app.core.settings.models.model_config import LLMModelSpec
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import (
        DeploymentConfig,
        TenantConfig,
        UserEntitlementConfig,
    )
    from app.inference_routing.credential_resolver import CredentialSelection


class ResolvedExecutionContextFactory:
    """Assemble immutable execution contexts from resolved routing components.

    Why centralize here:
        Without a factory, context assembly would be duplicated across pipeline
        branches, increasing drift risk in defaults, quota keys, or fingerprints.
    """

    def build_for_deployment(
        self,
        *,
        tenant_config: TenantConfig,
        deployment_config: DeploymentConfig,
        provider_static_config: ProviderStaticConfig,
        model_spec: LLMModelSpec,
        credential_selection: CredentialSelection,
    ) -> ResolvedExecutionContext:
        """Build context for tenant deployment routing path.

        Includes deployment overrides for timeout/retries/temperature/max_tokens
        when those overrides are configured.
        """
        effective_max_tokens = deployment_config.default_max_tokens or model_spec.max_output_tokens
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
                deployment_config.timeout_seconds or provider_static_config.default_timeout_seconds
            ),
            effective_max_retries=(
                deployment_config.max_retries
                if deployment_config.max_retries is not None
                else provider_static_config.default_max_retries
            ),
            effective_temperature=deployment_config.default_temperature,
            effective_max_tokens=effective_max_tokens,
            extra_headers=deployment_config.extra_headers,
            extra_config=deployment_config.extra_config,
            quota_key=deployment_config.deployment_key,
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
        """Build context for user entitlement override path.

        Entitlement path uses provider defaults for timeout/retry/temperature
        because deployment-level override values are not the authoritative
        source for user-owned entitlement credentials.
        """
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
            extra_headers={},
            extra_config=user_entitlement_config.extra_config,
            quota_key=str(user_entitlement_config.entitlement_id),
            route_fingerprint=route_fingerprint,
        )

    @staticmethod
    def _compute_route_fingerprint(**payload: str | None) -> str:
        """Compute stable opaque digest for resolved route attributes.

        Rationale:
            Fingerprints support cache correlation and route identity checks
            without exposing raw secret references in logs or keys.
        """
        normalized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
