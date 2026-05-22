"""
Tenant Access Service
=====================

Tenant-scoped permission checks for management API workflows.

This module answers questions like:
    - Can this caller read tenant resources?
    - Can this caller perform tenant-admin actions?
    - Can this caller act on another user's data?

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

Step-by-step relation for tenant checks:
    1. Route-level JWT/role guards authenticate the caller.
    2. Service-layer code calls ``ensure_tenant_read`` or ``ensure_tenant_admin``.
    3. Access service inspects platform role first, then tenant membership data.
    4. On failure, a typed ``TenantAccessDeniedError`` is raised for consistent
       API error mapping.

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
    """Evaluate tenant-related management permissions for authenticated users."""

    def __init__(self, membership_persistence: TenantMembershipPersistence) -> None:
        """Initialize with membership persistence used for tenant role lookups."""
        self._memberships = membership_persistence

    def is_platform_admin(self, current_user: AuthTokenPayload) -> bool:
        """Return ``True`` when caller has platform-wide admin-equivalent role."""
        return current_user.role in _ADMIN_ROLES

    def is_platform_operator(self, current_user: AuthTokenPayload) -> bool:
        """Return ``True`` when caller has platform-wide tenant read privileges."""
        return current_user.role in _OPERATOR_ROLES

    async def ensure_tenant_read(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Enforce tenant-read access policy.

        Access is allowed when either condition is true:
            - caller has platform operator-or-higher role, or
            - caller is an active member of the target tenant.
        """
        if self.is_platform_operator(current_user):
            return
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if membership and membership.get("status") == _ACTIVE_STATUS:
            return
        raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "member")

    async def ensure_tenant_admin(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Enforce tenant-admin access policy for mutating operations.

        Access is allowed when caller is platform admin-equivalent, or when
        tenant membership exists, is active, and has tenant role admin/owner.
        """
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
        """Allow access only to self-owned resources or platform admins.

        Used for user-centric endpoints where callers must not inspect or
        mutate other users' data unless they hold elevated platform privileges.
        """
        if user_id == current_user.user_id or self.is_platform_admin(current_user):
            return
        raise TenantAccessDeniedError(str(current_user.user_id), str(user_id), "self_or_admin")
