"""
Credential Resolver
===================

Chooses which credential reference and API endpoint to use for a resolved
route, without ever touching actual passwords or API keys.

Why never fetch the actual secret here?
    This resolver returns a *reference* (like a key name or path), not the
    secret value itself. The actual secret retrieval happens in the provider
    layer, right before the outbound call. This separation means routing logic
    never has access to plaintext credentials — a security boundary that
    limits blast radius if routing code is compromised.

Enterprise Pattern: Credential Reference Pattern
    Routing returns a pointer to a secret; secret materialization stays in
    the secrets-management layer. Routing and secrets never mix in memory.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from app.inference_routing.models import CredentialScope

if TYPE_CHECKING:
    from app.core.settings.models.tenant_config import DeploymentConfig, UserEntitlementConfig


class CredentialSelection(NamedTuple):
    """Resolved route-specific credential reference and endpoint details."""

    credential_scope: CredentialScope
    secret_reference: str
    api_endpoint_url: str
    cloud_region: str | None


class CredentialResolver:
    """Chooses credential references without fetching plaintext secrets."""

    @staticmethod
    def from_user_entitlement(
        entitlement: UserEntitlementConfig,
    ) -> CredentialSelection:
        """Resolve credential reference data from a user entitlement."""
        return CredentialSelection(
            credential_scope=CredentialScope.USER,
            secret_reference=entitlement.secret_reference,
            api_endpoint_url=entitlement.api_endpoint_url,
            cloud_region=entitlement.cloud_region,
        )

    @staticmethod
    def from_deployment(
        deployment: DeploymentConfig,
    ) -> CredentialSelection:
        """Resolve credential reference data from a tenant deployment."""
        return CredentialSelection(
            credential_scope=CredentialScope.TENANT,
            secret_reference=deployment.secret_reference,
            api_endpoint_url=deployment.api_endpoint_url,
            cloud_region=deployment.cloud_region,
        )
