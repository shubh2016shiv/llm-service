"""
Resolution Service Contracts
============================

Defines the reader protocols consumed by the resolution services.

Architecture:
-------------
    request_resolution_service.py
        │
        ├── tenant_resolution_service.py ───────▶ TenantConfigReader
        ├── user_entitlement_resolution_service.py ─▶ UserEntitlementReader
        └── deployment_resolution_service.py ───▶ DeploymentResolver

Dependencies:
    - app.core.settings.models.tenant_config — tenant and entitlement models

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import TenantConfig, UserEntitlementConfig


class TenantConfigReader(Protocol):
    """Loads tenant runtime configuration for a given tenant identifier."""

    async def get_tenant_config(self, tenant_id: UUID | str) -> TenantConfig | None:
        """Return the tenant config for the given identifier, or None when missing."""


class UserEntitlementReader(Protocol):
    """Finds user entitlement candidates for deployment-key-driven requests."""

    async def find_matching_entitlements(
        self,
        tenant_id: UUID | str,
        user_id: UUID | str,
        deployment_key: str,
        requested_model_name: str | None = None,
    ) -> list[UserEntitlementConfig]:
        """Return candidate entitlements for a deployment-key-driven request."""
        ...
