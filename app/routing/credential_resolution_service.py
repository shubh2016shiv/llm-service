"""
Credential Resolution Service
=============================

Selects the credential reference and endpoint details for a resolved route.

Architecture:
-------------
    request_resolution_service.py
        │
        ├── user_entitlement_resolution_service.py
        ├── deployment_resolution_service.py
        └── credential_resolution_service.py
                │
                └── resolved_execution_context_factory.py

Dependencies:
    - app.core.settings.models.tenant_config — deployment and entitlement models
    - app.routing.resolution_models — credential scope enum

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from typing import NamedTuple

from app.core.settings.models.tenant_config import DeploymentConfig, UserEntitlementConfig
from app.routing.resolution_models import CredentialScope


class CredentialSelection(NamedTuple):
    """Resolved route-specific credential reference and endpoint details."""

    credential_scope: CredentialScope
    secret_reference: str
    api_endpoint_url: str
    cloud_region: str | None


class CredentialResolutionService:
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
