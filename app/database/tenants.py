"""
TenantPersistence
-----------------
PostgreSQL CRUD for the `tenants` table.

Tenants are the top-level isolation boundary. Every deployment, membership, and
entitlement is scoped to a tenant. The schema enforces a unique slug per tenant
so that URL-safe identifiers remain stable across renames.

allowed_provider_names = NULL means the tenant may use any active provider.
A non-null array restricts the tenant to the listed provider names.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.tenant_queries import (
    CHECK_TENANT_EXISTS_BY_ID_SQL,
    CHECK_TENANT_EXISTS_BY_SLUG_SQL,
    CREATE_TENANT_SQL,
    DELETE_TENANT_BY_ID_SQL,
    GET_TENANT_BY_ID_SQL,
    GET_TENANT_BY_SLUG_SQL,
    GET_TENANT_FOR_ROUTING_BY_ID_SQL,
    build_tenant_count_query,
    build_tenant_list_query,
)
from app.database.session import DatabaseSessionManager
from app.schemas.management_filters import TenantListFilters

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_VALID_STATUSES: list[str] = ["active", "trial", "suspended", "deleted"]
_VALID_TIERS: list[str] = ["free", "starter", "professional", "enterprise"]


class TenantPersistence(BasePersistence):
    """Persistence for tenant lifecycle management."""

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================

    async def tenant_exists(self, tenant_id: UUID) -> bool:
        """Return True if a tenant with this UUID exists."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_TENANT_EXISTS_BY_ID_SQL), {"tenant_id": str(tenant_id)}
            )
            return result.first() is not None

    async def slug_exists(self, tenant_slug: str) -> bool:
        """Return True if the slug is already taken."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_TENANT_EXISTS_BY_SLUG_SQL), {"tenant_slug": tenant_slug}
            )
            return result.first() is not None

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_tenant(
        self,
        tenant_name: str,
        tenant_slug: str,
        tier: str = "free",
        status: str = "active",
        rate_limit_requests_per_minute: int = 1000,
        rate_limit_tokens_per_minute: int = 100_000,
        rate_limit_concurrent_requests: int = 10,
        allowed_provider_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new tenant record.

        Args:
            tenant_name: Human-readable organisation name.
            tenant_slug: URL-safe slug, must match '^[a-z0-9]+(-[a-z0-9]+)*$'.
            tier: Billing tier. Defaults to 'free'.
            status: Lifecycle status. Defaults to 'active'.
            rate_limit_requests_per_minute: Org-wide RPM ceiling.
            rate_limit_tokens_per_minute: Org-wide TPM ceiling.
            rate_limit_concurrent_requests: Max concurrent requests.
            allowed_provider_names: Optional provider allow-list.
                None means all active providers are allowed.

        Returns:
            Created tenant row dict.

        Raises:
            ValueError: On validation failure or duplicate slug.
        """
        self.validate_string_not_empty(tenant_name, "tenant_name")
        self.validate_string_not_empty(tenant_slug, "tenant_slug")
        self.validate_enum_value(tier, _VALID_TIERS, "tier")
        self.validate_enum_value(status, _VALID_STATUSES, "status")
        self.validate_positive_integer(
            rate_limit_requests_per_minute, "rate_limit_requests_per_minute"
        )
        self.validate_positive_integer(rate_limit_tokens_per_minute, "rate_limit_tokens_per_minute")
        self.validate_positive_integer(
            rate_limit_concurrent_requests, "rate_limit_concurrent_requests"
        )

        if await self.slug_exists(tenant_slug):
            raise ValueError(f"Tenant slug '{tenant_slug}' is already in use")

        params = {
            "tenant_name": tenant_name,
            "tenant_slug": tenant_slug,
            "status": status,
            "tier": tier,
            "rate_limit_requests_per_minute": rate_limit_requests_per_minute,
            "rate_limit_tokens_per_minute": rate_limit_tokens_per_minute,
            "rate_limit_concurrent_requests": rate_limit_concurrent_requests,
            "allowed_provider_names": allowed_provider_names,
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_TENANT_SQL), params)
                row = result.mappings().one_or_none()
                if not row:
                    raise RuntimeError("INSERT returned no row")
                logger.info(
                    "TenantPersistence: created tenant — slug=%s id=%s",
                    tenant_slug,
                    row["tenant_id"],
                )
                return dict(row)
        except (ValueError, RuntimeError):
            raise
        except Exception:
            logger.error(
                "TenantPersistence: create_tenant failed — slug=%s", tenant_slug, exc_info=True
            )
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_tenant_by_id(self, tenant_id: UUID | str) -> dict[str, Any] | None:
        """Return a tenant by UUID, or None if not found."""
        self.validate_uuid(tenant_id, "tenant_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_TENANT_BY_ID_SQL), {"tenant_id": str(tenant_id)}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantPersistence: get_tenant_by_id failed — id=%s", tenant_id, exc_info=True
            )
            raise

    async def get_tenant_config_for_routing(self, tenant_id: UUID | str) -> dict[str, Any] | None:
        """Return the explicit-column routing projection for a tenant, or None if not found.

        Uses a fixed column list so the routing layer is never silently affected
        by columns added to the tenants table in the future.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_TENANT_FOR_ROUTING_BY_ID_SQL), {"tenant_id": str(tenant_id)}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantPersistence: get_tenant_config_for_routing failed — id=%s",
                tenant_id,
                exc_info=True,
            )
            raise

    async def get_tenant_by_slug(self, tenant_slug: str) -> dict[str, Any] | None:
        """Return a tenant by its slug, or None if not found."""
        self.validate_string_not_empty(tenant_slug, "tenant_slug")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_TENANT_BY_SLUG_SQL), {"tenant_slug": tenant_slug}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantPersistence: get_tenant_by_slug failed — slug=%s", tenant_slug, exc_info=True
            )
            raise

    async def list_tenants(
        self,
        filters: TenantListFilters,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a paginated tenant list with optional status/tier filters."""
        self.validate_pagination_parameters(limit, offset)
        if filters.status_filter:
            self.validate_enum_value(filters.status_filter, _VALID_STATUSES, "status_filter")
        if filters.tier_filter:
            self.validate_enum_value(filters.tier_filter, _VALID_TIERS, "tier_filter")
        sql, params = build_tenant_list_query(filters, limit, offset)

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error("TenantPersistence: list_tenants failed", exc_info=True)
            raise

    async def count_tenants(self, filters: TenantListFilters) -> int:
        """Return tenant count for the supplied filters."""
        if filters.status_filter:
            self.validate_enum_value(filters.status_filter, _VALID_STATUSES, "status_filter")
        if filters.tier_filter:
            self.validate_enum_value(filters.tier_filter, _VALID_TIERS, "tier_filter")
        sql, params = build_tenant_count_query(filters)
        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("TenantPersistence: count_tenants failed", exc_info=True)
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_tenant(
        self,
        tenant_id: UUID,
        tenant_name: str | None = None,
        status: str | None = None,
        tier: str | None = None,
        rate_limit_requests_per_minute: int | None = None,
        rate_limit_tokens_per_minute: int | None = None,
        rate_limit_concurrent_requests: int | None = None,
        allowed_provider_names: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Partially update a tenant. Returns updated row or None."""
        self.validate_uuid(tenant_id, "tenant_id")

        update_fields: dict[str, Any] = {}
        if tenant_name is not None:
            self.validate_string_not_empty(tenant_name, "tenant_name")
            update_fields["tenant_name"] = tenant_name
        if status is not None:
            self.validate_enum_value(status, _VALID_STATUSES, "status")
            update_fields["status"] = status
        if tier is not None:
            self.validate_enum_value(tier, _VALID_TIERS, "tier")
            update_fields["tier"] = tier
        if rate_limit_requests_per_minute is not None:
            self.validate_positive_integer(
                rate_limit_requests_per_minute, "rate_limit_requests_per_minute"
            )
            update_fields["rate_limit_requests_per_minute"] = rate_limit_requests_per_minute
        if rate_limit_tokens_per_minute is not None:
            self.validate_positive_integer(
                rate_limit_tokens_per_minute, "rate_limit_tokens_per_minute"
            )
            update_fields["rate_limit_tokens_per_minute"] = rate_limit_tokens_per_minute
        if rate_limit_concurrent_requests is not None:
            self.validate_positive_integer(
                rate_limit_concurrent_requests, "rate_limit_concurrent_requests"
            )
            update_fields["rate_limit_concurrent_requests"] = rate_limit_concurrent_requests
        if allowed_provider_names is not None:
            update_fields["allowed_provider_names"] = allowed_provider_names

        if not update_fields:
            return await self.get_tenant_by_id(tenant_id)

        sql, params = self.build_dynamic_update_query(
            table_name="tenants",
            update_fields=update_fields,
            where_clause="tenant_id = :tenant_id",
            where_parameters={"tenant_id": str(tenant_id)},
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                row = result.mappings().one_or_none()
                if row:
                    self.log_operation("UPDATE", tenant_id)
                    return dict(row)
                return None
        except Exception:
            logger.error(
                "TenantPersistence: update_tenant failed — id=%s", tenant_id, exc_info=True
            )
            raise

    async def suspend_tenant(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Set tenant status to 'suspended'."""
        return await self.update_tenant(tenant_id=tenant_id, status="suspended")

    async def activate_tenant(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Set tenant status to 'active'."""
        return await self.update_tenant(tenant_id=tenant_id, status="active")

    # =========================================================================
    # DELETE
    # =========================================================================

    async def delete_tenant(self, tenant_id: UUID) -> bool:
        """Permanently delete a tenant. CASCADE removes memberships, deployments, entitlements.

        Returns:
            True if deleted; False if not found.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_TENANT_BY_ID_SQL), {"tenant_id": str(tenant_id)}
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", tenant_id)
                return bool(deleted)
        except Exception:
            logger.error(
                "TenantPersistence: delete_tenant failed — id=%s", tenant_id, exc_info=True
            )
            raise
