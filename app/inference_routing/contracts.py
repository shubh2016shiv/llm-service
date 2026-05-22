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

Architecture decision:
    Keep resolvers unaware of SQL, cache keys, and storage-specific models.
    Contracts express only the data shape and lookup intent needed for routing.

Step-by-step relation:
    1. Resolver calls protocol method.
    2. Concrete adapter fulfills protocol (DB/cache/API as needed).
    3. Resolver applies routing policy on returned typed config objects.
    4. Pipeline combines resolver outcomes into execution context.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import (
        DeploymentConfig,
        TenantConfig,
        UserEntitlementConfig,
    )


class TenantConfigReader(Protocol):
    """Read tenant-level runtime config needed for routing policy checks."""

    async def get_tenant_config(self, tenant_id: UUID | str) -> TenantConfig | None:
        """Return the tenant config for the given identifier, or None when missing."""


class DeploymentConfigReader(Protocol):
    """Read deployment-level routing config for a tenant deployment key.

    Includes endpoint/model/credential-reference metadata required by routing.
    """

    async def get_deployment_config(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
    ) -> DeploymentConfig | None:
        """Return the DeploymentConfig for the given route, or None when not found."""


class UserEntitlementReader(Protocol):
    """Find user entitlement candidates for user-override precedence rules."""

    async def find_matching_entitlements(
        self,
        tenant_id: UUID | str,
        user_id: UUID | str,
        deployment_key: str,
        requested_model_name: str | None = None,
        entitlement_id: UUID | None = None,
    ) -> list[UserEntitlementConfig]:
        """Return candidate entitlements for a deployment-key-driven request.

        When entitlement_id is supplied the result set is constrained to that
        single record, aligning the pipeline with the pre-authorized route from
        the auth layer.
        """
        ...
