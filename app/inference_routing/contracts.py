"""
Inference Routing Contracts
===========================

Protocol contracts (interfaces) that resolver services depend on.

Why protocols instead of direct database/Redis calls?
    Each resolver needs to read data — tenant config, user entitlements —
    but it shouldn't care WHERE that data comes from. By defining a protocol
    (a Python ``Protocol`` class that declares method signatures without
    implementations), the resolver only knows "I can call ``get_tenant_config()``"
    and never knows whether that call hits PostgreSQL, Redis, or a test mock.
    This makes resolvers testable in isolation and keeps storage decisions
    swappable.

Enterprise Pattern: Dependency Inversion Pattern
    High-level resolvers depend on abstract reader interfaces, not on concrete
    database classes. The actual database adapter implements the protocol.
    This flips the usual dependency direction — the resolver defines what it
    needs, and the persistence layer fulfills that contract.

Author: Shubham Singh
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
