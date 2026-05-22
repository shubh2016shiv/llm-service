"""
User Entitlement Resolver
=========================

Attempts to route a request through a user-specific entitlement before falling
back to tenant deployment routing.

Rules:
    - only active entitlements are considered
    - exactly one active match is required
    - selected provider must still be tenant-allowed

Enterprise Pattern: Precedence Rule Pattern
    User override wins only when it is valid and unambiguous.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inference_routing.exceptions import AmbiguousUserEntitlementError

if TYPE_CHECKING:
    from app.core.settings.models.tenant_config import TenantConfig, UserEntitlementConfig
    from app.inference_routing.contracts import UserEntitlementReader
    from app.inference_routing.models import ResolutionRequest
    from app.inference_routing.tenant_resolver import TenantResolver


class UserEntitlementResolver:
    """Resolves an optional user-scoped entitlement override."""

    def __init__(
        self,
        entitlement_reader: UserEntitlementReader,
        tenant_resolver: TenantResolver,
    ) -> None:
        self._entitlement_reader = entitlement_reader
        self._tenant_resolver = tenant_resolver

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

        active_candidates = [c for c in candidates if c.is_active]
        if not active_candidates:
            return None

        if len(active_candidates) > 1:
            raise AmbiguousUserEntitlementError(
                tenant_id=str(tenant_config.tenant_id),
                user_id=str(request.user_id),
                deployment_key=request.deployment_key,
            )

        entitlement = active_candidates[0]
        self._tenant_resolver.ensure_provider_allowed(tenant_config, entitlement.provider_name)
        return entitlement

