"""
Tenant Access Service
=====================

This module contains easy-to-follow permission checks used by management APIs.
It decides whether a user can read or update tenant-related resources.

Enterprise Pattern: Service + Repository Pattern
    - Service: ``TenantAccessService`` contains access rules.
    - Repository: ``TenantMembershipPersistence`` provides membership data.
    This keeps decision logic separate from data access logic.

How the flow works:
    API auth guard
        |
        v
    TenantAccessService
        |
        v
    TenantMembershipPersistence

Dependencies:
    - app.database.tenant_memberships: Reads tenant membership records.
    - app.schemas.auth_schema: Provides typed authenticated user payload.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import TenantAccessDeniedError
from app.database import TenantMembershipPersistence
from app.schemas.auth_schema import AuthTokenPayload

if TYPE_CHECKING:
    from uuid import UUID

_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "owner"})
_OPERATOR_ROLES: frozenset[str] = frozenset({"operator", "admin", "owner"})
_ACTIVE_STATUS = "active"


class TenantAccessService:
    """Check if a user has enough permissions for management operations."""

    def __init__(self, membership_persistence: TenantMembershipPersistence) -> None:
        """Store the membership data source used by permission checks."""
        self._memberships = membership_persistence

    def is_platform_admin(self, current_user: AuthTokenPayload) -> bool:
        """Return True when the user has platform admin-level privileges."""
        return current_user.role in _ADMIN_ROLES

    def is_platform_operator(self, current_user: AuthTokenPayload) -> bool:
        """Return True when the user can read platform-wide tenant data."""
        return current_user.role in _OPERATOR_ROLES

    async def ensure_tenant_read(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow reads only for platform operators or active members of that tenant."""
        if self.is_platform_operator(current_user):
            return
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if membership and membership.get("status") == _ACTIVE_STATUS:
            return
        raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "member")

    async def ensure_tenant_admin(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow updates only for platform admins or active tenant admins/owners."""
        if self.is_platform_admin(current_user):
            return
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if not membership:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "admin")
        if membership.get("status") != _ACTIVE_STATUS:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "admin")
        if membership.get("tenant_role") not in _ADMIN_ROLES:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "admin")

    def ensure_self_or_admin(self, user_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow access only if the target user is self, or caller is platform admin."""
        if user_id == current_user.user_id or self.is_platform_admin(current_user):
            return
        raise TenantAccessDeniedError(str(current_user.user_id), str(user_id), "self_or_admin")
