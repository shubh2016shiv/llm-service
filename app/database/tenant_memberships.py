"""
TenantMembershipPersistence
---------------------------
PostgreSQL CRUD for the `tenant_memberships` table.

A membership connects a user to a tenant and carries that user's role within
the tenant. One user can have different roles in different tenants.

The DB enforces UNIQUE (tenant_id, user_id) — one membership record per user
per tenant. Attempting to add the same user twice raises an IntegrityError;
the service layer should call check_membership_exists() first to return a
clean ValueError before hitting the DB constraint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.tenant_membership_queries import (
    CHECK_MEMBERSHIP_EXISTS_SQL,
    COUNT_TENANTS_FOR_USER_SQL,
    CREATE_MEMBERSHIP_SQL,
    DELETE_MEMBERSHIP_BY_ID_SQL,
    DELETE_MEMBERSHIP_BY_TENANT_AND_USER_SQL,
    GET_MEMBERSHIP_BY_ID_SQL,
    GET_MEMBERSHIP_BY_TENANT_AND_USER_SQL,
    LIST_MEMBERSHIPS_BY_USER_SQL,
    build_tenant_membership_count_query,
    build_tenant_membership_list_query,
)
from app.database.session import DatabaseSessionManager
from app.schemas.management_filters import TenantMembershipListFilters

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_VALID_TENANT_ROLES: list[str] = ["owner", "admin", "developer", "viewer", "operator"]
_VALID_STATUSES: list[str] = ["active", "suspended", "inactive"]


class TenantMembershipPersistence(BasePersistence):
    """Persistence for user-to-tenant role assignments."""

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================

    async def membership_exists(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Return True if a membership already exists for this (tenant, user) pair."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_MEMBERSHIP_EXISTS_SQL),
                {"tenant_id": str(tenant_id), "user_id": str(user_id)},
            )
            return result.first() is not None

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_membership(
        self,
        tenant_id: UUID,
        user_id: UUID,
        created_by_user_id: UUID,
        tenant_role: str = "developer",
        status: str = "active",
    ) -> dict[str, Any]:
        """Add a user to a tenant with the given role.

        Args:
            tenant_id: The tenant to add the user to.
            user_id: The user being added.
            created_by_user_id: Admin who issued the membership.
            tenant_role: Role within the tenant. Defaults to 'developer'.
            status: Membership status. Defaults to 'active'.

        Returns:
            Created membership row dict.

        Raises:
            ValueError: On validation failure or duplicate (tenant, user).
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        self.validate_uuid(created_by_user_id, "created_by_user_id")
        self.validate_enum_value(tenant_role, _VALID_TENANT_ROLES, "tenant_role")
        self.validate_enum_value(status, _VALID_STATUSES, "status")

        if await self.membership_exists(tenant_id, user_id):
            raise ValueError(
                f"User '{user_id}' already has a membership in tenant '{tenant_id}'. "
                "Use update_membership() to change the role or status."
            )

        params = {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "tenant_role": tenant_role,
            "status": status,
            "created_by_user_id": str(created_by_user_id),
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_MEMBERSHIP_SQL), params)
                row = result.mappings().one_or_none()
                if not row:
                    raise RuntimeError("INSERT returned no row")
                logger.info(
                    "TenantMembershipPersistence: created membership — tenant=%s user=%s role=%s",
                    tenant_id,
                    user_id,
                    tenant_role,
                )
                return dict(row)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            self.raise_for_foreign_key_violation(
                exc,
                {
                    "tenant_memberships_tenant_id_fkey": ("Tenant", str(tenant_id)),
                    "tenant_memberships_user_id_fkey": ("User", str(user_id)),
                    "tenant_memberships_created_by_user_id_fkey": (
                        "User",
                        str(created_by_user_id),
                    ),
                },
            )
            logger.error(
                "TenantMembershipPersistence: create_membership failed — tenant=%s user=%s",
                tenant_id,
                user_id,
                exc_info=True,
            )
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_membership_by_id(self, membership_id: UUID | str) -> dict[str, Any] | None:
        """Return a membership by its UUID."""
        self.validate_uuid(membership_id, "membership_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_MEMBERSHIP_BY_ID_SQL), {"membership_id": str(membership_id)}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantMembershipPersistence: get_membership_by_id failed — id=%s",
                membership_id,
                exc_info=True,
            )
            raise

    async def get_membership(self, tenant_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        """Return the membership for a specific (tenant, user) pair."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_MEMBERSHIP_BY_TENANT_AND_USER_SQL),
                    {"tenant_id": str(tenant_id), "user_id": str(user_id)},
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error("TenantMembershipPersistence: get_membership failed", exc_info=True)
            raise

    async def list_tenant_memberships(
        self,
        tenant_id: UUID,
        filters: TenantMembershipListFilters,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return memberships for a tenant, with optional role and status filters."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_pagination_parameters(limit, offset)
        if filters.tenant_role_filter:
            self.validate_enum_value(
                filters.tenant_role_filter,
                _VALID_TENANT_ROLES,
                "tenant_role_filter",
            )
        sql, params = build_tenant_membership_list_query(str(tenant_id), filters, limit, offset)

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "TenantMembershipPersistence: list_tenant_memberships failed — tenant=%s",
                tenant_id,
                exc_info=True,
            )
            raise

    async def list_user_memberships(
        self, user_id: UUID, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return all tenants a user belongs to."""
        self.validate_uuid(user_id, "user_id")
        self.validate_pagination_parameters(limit, offset)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_MEMBERSHIPS_BY_USER_SQL),
                    {"user_id": str(user_id), "limit": limit, "offset": offset},
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "TenantMembershipPersistence: list_user_memberships failed — user=%s",
                user_id,
                exc_info=True,
            )
            raise

    async def count_tenant_members(
        self,
        tenant_id: UUID,
        filters: TenantMembershipListFilters,
    ) -> int:
        """Return member count for a tenant."""
        self.validate_uuid(tenant_id, "tenant_id")
        if filters.tenant_role_filter:
            self.validate_enum_value(
                filters.tenant_role_filter,
                _VALID_TENANT_ROLES,
                "tenant_role_filter",
            )
        sql, params = build_tenant_membership_count_query(str(tenant_id), filters)
        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("TenantMembershipPersistence: count_tenant_members failed", exc_info=True)
            raise

    async def count_user_tenants(self, user_id: UUID) -> int:
        """Return the number of tenants a user belongs to."""
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(COUNT_TENANTS_FOR_USER_SQL), {"user_id": str(user_id)}
                )
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("TenantMembershipPersistence: count_user_tenants failed", exc_info=True)
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_membership(
        self,
        membership_id: UUID,
        tenant_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """Update role or status on a membership. Returns updated row or None."""
        self.validate_uuid(membership_id, "membership_id")

        update_fields: dict[str, Any] = {}
        if tenant_role is not None:
            self.validate_enum_value(tenant_role, _VALID_TENANT_ROLES, "tenant_role")
            update_fields["tenant_role"] = tenant_role
        if status is not None:
            self.validate_enum_value(status, _VALID_STATUSES, "status")
            update_fields["status"] = status

        if not update_fields:
            return await self.get_membership_by_id(membership_id)

        sql, params = self.build_dynamic_update_query(
            table_name="tenant_memberships",
            update_fields=update_fields,
            where_clause="membership_id = :membership_id",
            where_parameters={"membership_id": str(membership_id)},
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                row = result.mappings().one_or_none()
                if row:
                    self.log_operation("UPDATE", membership_id)
                    return dict(row)
                return None
        except Exception:
            logger.error(
                "TenantMembershipPersistence: update_membership failed — id=%s",
                membership_id,
                exc_info=True,
            )
            raise

    async def promote_to_admin(self, membership_id: UUID) -> dict[str, Any] | None:
        """Set the membership tenant_role to 'admin'."""
        return await self.update_membership(membership_id=membership_id, tenant_role="admin")

    async def suspend_membership(self, membership_id: UUID) -> dict[str, Any] | None:
        """Set membership status to 'suspended'."""
        return await self.update_membership(membership_id=membership_id, status="suspended")

    # =========================================================================
    # DELETE
    # =========================================================================

    async def delete_membership_by_id(self, membership_id: UUID) -> bool:
        """Delete a membership by its UUID.

        Returns:
            True if deleted; False if not found.
        """
        self.validate_uuid(membership_id, "membership_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_MEMBERSHIP_BY_ID_SQL), {"membership_id": str(membership_id)}
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", membership_id)
                return bool(deleted)
        except Exception:
            logger.error(
                "TenantMembershipPersistence: delete_membership_by_id failed — id=%s",
                membership_id,
                exc_info=True,
            )
            raise

    async def remove_user_from_tenant(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Remove a user's membership from a tenant entirely.

        Returns:
            True if the membership existed and was removed; False if not found.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_MEMBERSHIP_BY_TENANT_AND_USER_SQL),
                    {"tenant_id": str(tenant_id), "user_id": str(user_id)},
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", f"tenant={tenant_id} user={user_id}")
                return bool(deleted)
        except Exception:
            logger.error(
                "TenantMembershipPersistence: remove_user_from_tenant failed",
                exc_info=True,
            )
            raise
