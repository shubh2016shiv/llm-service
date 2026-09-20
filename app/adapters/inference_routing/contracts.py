"""Narrow input contracts for the inference-routing adapter.

Architecture:
    PostgresInferenceRoutingConfigReader -> these Protocols -> PostgreSQL adapters

A Protocol describes the small shape this reader needs. The production
PostgreSQL classes satisfy these contracts naturally, while tests can
use readable fakes without pretending that those fakes are production classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID


class TenantRoutingPersistence(Protocol):
    """Read the tenant projection used during route resolution."""

    async def get_tenant_config_for_routing(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Return one raw tenant projection, or ``None`` when absent."""
        ...


class EntitlementRoutingPersistence(Protocol):
    """Read the one authorization-approved entitlement projection."""

    async def get_routing_entitlement_for_route(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
        entitlement_id: UUID,
    ) -> dict[str, Any] | None:
        """Return the exact active entitlement projection, or ``None``."""
        ...
