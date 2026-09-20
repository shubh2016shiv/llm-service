"""Read fresh inference-routing projections from PostgreSQL.

Architecture:
    InferenceRouteResolver -> PostgresInferenceRoutingConfigReader
                           -> narrow persistence protocols -> PostgreSQL

Both reads are deliberately uncached. Tenant suspension and entitlement
revocation are security decisions, so the next request must observe them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.inference_routing.routing_config_mappers import (
    convert_entitlement_row,
    convert_tenant_row,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.adapters.inference_routing.contracts import (
        EntitlementRoutingPersistence,
        TenantRoutingPersistence,
    )
    from app.core.settings.models.tenant_config import TenantConfig, UserEntitlementConfig
    from app.inference_routing.models import ResolutionRequest


class PostgresInferenceRoutingConfigReader:
    """Translate authoritative SQL projections into routing domain models."""

    def __init__(
        self,
        tenant_persistence: TenantRoutingPersistence,
        entitlement_persistence: EntitlementRoutingPersistence,
    ) -> None:
        """Store the two focused persistence capabilities."""
        self._tenant_persistence = tenant_persistence
        self._entitlement_persistence = entitlement_persistence

    async def read_tenant_config(self, tenant_id: UUID) -> TenantConfig | None:
        """Return current tenant policy directly from PostgreSQL."""
        row = await self._tenant_persistence.get_tenant_config_for_routing(tenant_id)
        return convert_tenant_row(row) if row is not None else None

    async def read_entitlement_config(
        self,
        request: ResolutionRequest,
    ) -> UserEntitlementConfig | None:
        """Return the exact active entitlement selected by authorization."""
        row = await self._entitlement_persistence.get_routing_entitlement_for_route(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            deployment_key=request.deployment_key,
            entitlement_id=request.entitlement_id,
        )
        return convert_entitlement_row(row) if row is not None else None
