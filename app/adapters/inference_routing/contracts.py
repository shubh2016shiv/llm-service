"""Narrow input contracts for the inference-routing adapter.

Architecture:
    CachedInferenceRoutingConfigReader -> these Protocols -> concrete adapters

A Protocol describes the small shape this reader needs. The production
PostgreSQL and Redis classes satisfy these contracts naturally, while tests can
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
    """Read user-entitlement projections relevant to one route request."""

    async def list_routing_entitlements_for_route(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
        requested_model_name: str | None = None,
        entitlement_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Return the matching raw entitlement projections."""
        ...


class DeploymentRoutingPersistence(Protocol):
    """Read a tenant deployment projection by its public key."""

    async def get_deployment_config_for_routing(
        self, tenant_id: UUID, deployment_key: str
    ) -> dict[str, Any] | None:
        """Return one raw deployment projection, or ``None`` when absent."""
        ...


class DeploymentCacheBackend(Protocol):
    """Provide the three byte-cache operations needed by deployment caching."""

    async def get(self, key: str) -> bytes | None:
        """Return cached bytes, or ``None`` for a miss/unavailable backend."""
        ...

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = 300) -> bool:
        """Store bytes and report whether the backend accepted them."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a key and report whether the command was processed."""
        ...
