"""
app/inference_routing/persistence_adapters.py — Routing protocol adapters.

Implements TenantConfigReader, UserEntitlementReader, and DeploymentConfigReader
by delegating to the app/database persistence layer and converting raw row dicts
to routing domain model objects.

No SQL is executed here. All database access goes through the persistence
classes in app/database/, which is the single authorised SQL execution boundary
in this codebase.

Architecture rationale:
    Resolvers depend on protocol contracts, while this module provides concrete
    adapters that translate database-row projections into typed routing models.
    This keeps policy logic separated from persistence-row shape details.

Step-by-step data path:
    1. Resolver calls protocol method.
    2. Adapter delegates to database persistence class.
    3. Adapter converts row dict to typed config model.
    4. Resolver applies policy checks on typed model.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    DeploymentStatus,
    TenantConfig,
    TenantRateLimits,
    TenantStatus,
    TenantTier,
    UserEntitlementConfig,
)
from app.database import (
    TenantDeploymentPersistence,
    TenantPersistence,
    UserEntitlementPersistence,
)

logger = logging.getLogger(__name__)


class DatabaseTenantConfigReader:
    """Protocol adapter that resolves tenant routing config from persistence."""

    def __init__(self, persistence: TenantPersistence | None = None) -> None:
        self._persistence = persistence or TenantPersistence()

    async def get_tenant_config(self, tenant_id: UUID | str) -> TenantConfig | None:
        """Return typed tenant config for routing, or ``None`` when not found."""
        row = await self._persistence.get_tenant_config_for_routing(tenant_id)
        if row is None:
            return None
        return _row_to_tenant_config(row)


class DatabaseUserEntitlementReader:
    """Protocol adapter that resolves entitlement candidates from persistence.

    Adapter role:
        Convert persistence result rows into ``UserEntitlementConfig`` objects
        that are safe for resolver-level policy logic.
    """

    def __init__(self, persistence: UserEntitlementPersistence | None = None) -> None:
        self._persistence = persistence or UserEntitlementPersistence()

    async def find_matching_entitlements(
        self,
        tenant_id: UUID | str,
        user_id: UUID | str,
        deployment_key: str,
        requested_model_name: str | None = None,
        entitlement_id: UUID | None = None,
    ) -> list[UserEntitlementConfig]:
        """Return active entitlements for the given routing key.

        When entitlement_id is provided the result is constrained to that single
        record, aligning with the pre-authorized route from the auth layer.
        """
        rows = await self._persistence.list_routing_entitlements_for_route(
            tenant_id=UUID(str(tenant_id)),
            user_id=UUID(str(user_id)),
            deployment_key=deployment_key,
            requested_model_name=requested_model_name,
            entitlement_id=entitlement_id,
        )
        return [_row_to_user_entitlement_config(row) for row in rows]


class DatabaseDeploymentConfigReader:
    """Protocol adapter that resolves deployment routing config from persistence."""

    def __init__(self, persistence: TenantDeploymentPersistence | None = None) -> None:
        self._persistence = persistence or TenantDeploymentPersistence()

    async def get_deployment_config(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
    ) -> DeploymentConfig | None:
        """Return typed deployment config for route, or ``None`` when missing."""
        row = await self._persistence.get_deployment_config_for_routing(tenant_id, deployment_key)
        if row is None:
            return None
        return _row_to_deployment_config(row)


# ---------------------------------------------------------------------------
# Row → domain model converters
# ---------------------------------------------------------------------------


def _row_to_tenant_config(row: dict[str, Any]) -> TenantConfig:
    """Convert tenant routing projection row into ``TenantConfig`` model.

    Rationale:
        Conversion is explicit so schema drift is visible and testable.
    """
    rate_limits = TenantRateLimits(
        rpm=int(row["rate_limit_requests_per_minute"]),
        tpm=int(row["rate_limit_tokens_per_minute"]),
        concurrent_requests=int(row["rate_limit_concurrent_requests"]),
    )
    raw_providers = row.get("allowed_provider_names")
    allowed_provider_names: frozenset[str] | None = (
        frozenset(raw_providers) if raw_providers else None
    )
    return TenantConfig(
        tenant_id=UUID(str(row["tenant_id"])),
        tenant_name=str(row["tenant_name"]),
        tenant_slug=str(row["tenant_slug"]),
        status=TenantStatus(str(row["status"])),
        tier=TenantTier(str(row["tier"])),
        rate_limits=rate_limits,
        allowed_provider_names=allowed_provider_names,
    )


def _row_to_deployment_config(row: dict[str, Any]) -> DeploymentConfig:
    """Convert deployment routing projection row into ``DeploymentConfig`` model."""
    return DeploymentConfig(
        deployment_id=UUID(str(row["deployment_id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        deployment_key=str(row["deployment_key"]),
        deployment_name=str(row["deployment_name"]),
        status=DeploymentStatus(str(row["status"])),
        provider_name=str(row["provider_name"]),
        model_name=str(row["model_name"]),
        api_endpoint_url=str(row["api_endpoint_url"]),
        secret_reference=str(row["secret_reference"]),
        cloud_region=row.get("cloud_region"),
        timeout_seconds=float(row["timeout_seconds"]) if row.get("timeout_seconds") is not None else None,
        max_retries=int(row["max_retries"]) if row.get("max_retries") is not None else None,
        default_temperature=float(row["default_temperature"]) if row.get("default_temperature") is not None else 0.7,
        default_max_tokens=int(row["default_max_output_tokens"]) if row.get("default_max_output_tokens") is not None else None,
        extra_headers=dict(row["extra_headers"]) if row.get("extra_headers") else {},
        extra_config=dict(row["extra_config"]) if row.get("extra_config") else {},
        is_default=bool(row.get("is_default", False)),
        priority=int(row.get("routing_priority", 0)),
    )


def _row_to_user_entitlement_config(row: dict[str, Any]) -> UserEntitlementConfig:
    """Convert entitlement routing projection row into ``UserEntitlementConfig`` model."""
    return UserEntitlementConfig(
        entitlement_id=UUID(str(row["entitlement_id"])),
        user_id=UUID(str(row["user_id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        entitlement_name=str(row["entitlement_name"]),
        provider_name=str(row["provider_name"]),
        model_name=str(row["model_name"]),
        api_endpoint_url=str(row["api_endpoint_url"]),
        secret_reference=str(row["secret_reference"]),
        cloud_provider=row.get("cloud_provider"),
        cloud_region=row.get("cloud_region"),
        extra_config=row.get("extra_config") or {},
        is_active=(str(row.get("status", "")) == "active"),
    )
