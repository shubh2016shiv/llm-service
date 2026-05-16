"""
Tenant Resolution Service
=========================

Loads tenant metadata and enforces tenant-level routing policy.

Architecture:
-------------
    request_resolution_service.py
        │
        └── tenant_resolution_service.py
                │
                └── TenantConfigReader

Dependencies:
    - app.routing.contracts — TenantConfigReader
    - app.core.settings.models.tenant_config — TenantConfig
    - app.core.exceptions — tenant error types

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import TenantNotFoundError, TenantSuspendedError
from app.routing.exceptions import ProviderNotAllowedError

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import TenantConfig
    from app.routing.contracts import TenantConfigReader


class TenantResolutionService:
    """Loads tenant metadata and enforces tenant-level routing policy."""

    def __init__(self, tenant_reader: TenantConfigReader) -> None:
        self._tenant_reader = tenant_reader

    async def resolve_tenant(self, tenant_id: UUID | str) -> TenantConfig:
        """Load the tenant configuration and enforce active status."""
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
        """Reject providers that are outside the tenant allow-list."""
        if not tenant_config.allows_provider(provider_name):
            raise ProviderNotAllowedError(str(tenant_config.tenant_id), provider_name)
