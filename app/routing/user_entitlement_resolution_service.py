"""
User Entitlement Resolution Service
===================================

Attempts to resolve a user-scoped entitlement override before falling back to
tenant deployment routing.

Architecture:
-------------
    request_resolution_service.py
        │
        └── user_entitlement_resolution_service.py
                │
                ├── UserEntitlementReader
                └── TenantResolutionService

Dependencies:
    - app.routing.contracts — UserEntitlementReader
    - app.routing.resolution_models — request contract
    - app.core.settings.models.tenant_config — tenant and entitlement models

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.routing.exceptions import AmbiguousUserEntitlementError

if TYPE_CHECKING:
    from app.core.settings.models.tenant_config import TenantConfig, UserEntitlementConfig
    from app.routing.contracts import UserEntitlementReader
    from app.routing.resolution_models import ResolutionRequest
    from app.routing.tenant_resolution_service import TenantResolutionService


class UserEntitlementResolutionService:
    """Resolves an optional user-scoped entitlement override."""

    def __init__(
        self,
        entitlement_reader: UserEntitlementReader,
        tenant_resolution_service: TenantResolutionService,
    ) -> None:
        self._entitlement_reader = entitlement_reader
        self._tenant_resolution_service = tenant_resolution_service

    async def resolve_override(
        self,
        tenant_config: TenantConfig,
        request: ResolutionRequest,
    ) -> UserEntitlementConfig | None:
        """Return a single active, tenant-allowed user entitlement if one matches."""
        candidates = await self._entitlement_reader.find_matching_entitlements(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            deployment_key=request.deployment_key,
            requested_model_name=request.requested_model_name,
        )

        active_candidates = [candidate for candidate in candidates if candidate.is_active]
        if not active_candidates:
            return None

        if len(active_candidates) > 1:
            raise AmbiguousUserEntitlementError(
                tenant_id=str(tenant_config.tenant_id),
                user_id=str(request.user_id),
                deployment_key=request.deployment_key,
            )

        entitlement = active_candidates[0]
        self._tenant_resolution_service.ensure_provider_allowed(
            tenant_config,
            entitlement.provider_name,
        )
        return entitlement
