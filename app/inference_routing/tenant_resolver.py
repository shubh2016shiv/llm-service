"""
Tenant Resolver
===============

Loads tenant runtime configuration and enforces tenant-level policy.

What it guarantees:
    - tenant exists
    - tenant is active
    - selected provider is allowed for this tenant

Enterprise Pattern: Policy Gate Pattern
    Tenant policy checks happen early so invalid routes fail fast.

Architecture rationale:
    Tenant state and policy are global guards for all downstream route choices.
    Running these checks first avoids unnecessary entitlement/deployment lookups
    for requests that are invalid at tenant scope.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import TenantNotFoundError, TenantSuspendedError
from app.inference_routing.exceptions import ProviderNotAllowedError

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import TenantConfig
    from app.inference_routing.contracts import TenantConfigReader


class TenantResolver:
    """Resolve tenant config and enforce tenant-level routing constraints.

    This resolver is intentionally narrow: it answers tenant policy questions
    only and does not perform provider/model capability checks.
    """

    def __init__(self, tenant_reader: TenantConfigReader) -> None:
        self._tenant_reader = tenant_reader

    async def resolve_tenant(self, tenant_id: UUID | str) -> TenantConfig:
        """Load tenant config and enforce tenant lifecycle eligibility.

        Raises:
            TenantNotFoundError: No tenant config exists for identifier.
            TenantSuspendedError: Tenant exists but cannot process requests.
        """
        tenant = await self._tenant_reader.get_tenant_config(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        if not tenant.is_active:
            raise TenantSuspendedError(str(tenant.tenant_id), reason=tenant.status.value)
        return tenant

    @staticmethod
    def ensure_provider_allowed(
        tenant_config: TenantConfig,
        provider_name: str,
    ) -> None:
        """Enforce tenant provider allow-list policy.

        Rationale:
            Even if deployment or entitlement references a provider, tenant
            policy may restrict that provider for compliance or cost reasons.
        """
        if not tenant_config.allows_provider(provider_name):
            raise ProviderNotAllowedError(str(tenant_config.tenant_id), provider_name)

