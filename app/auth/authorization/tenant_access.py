"""
Tenant Access Service
=====================

Centralizes tenant-scoped authorization checks for management services.

Architecture:
-------------
    API auth guard
        │
        ▼
    TenantAccessService
        │
        ▼
    TenantMembershipPersistence

Dependencies:
    - app.database.tenant_memberships — membership lookup
    - app.schemas.auth_schema — authenticated caller payload

Author: Engineering Team
Last Updated: 2026-05-18
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import TenantAccessDeniedError
from app.database import TenantMembershipPersistence
from app.schemas.auth_schema import AuthTokenPayload

_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "owner"})
_OPERATOR_ROLES: frozenset[str] = frozenset({"operator", "admin", "owner"})
_ACTIVE_STATUS = "active"


class TenantAccessService:
    """Verify platform and tenant-scoped authorization for management services."""

    def __init__(self, membership_persistence: TenantMembershipPersistence) -> None:
        """Initialize with injected membership persistence."""
        self._memberships = membership_persistence

    def is_platform_admin(self, current_user: AuthTokenPayload) -> bool:
        """Return True when the JWT role grants platform admin authority."""
        return current_user.role in _ADMIN_ROLES

    def is_platform_operator(self, current_user: AuthTokenPayload) -> bool:
        """Return True when the JWT role grants platform-wide read authority."""
        return current_user.role in _OPERATOR_ROLES

    async def ensure_tenant_read(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow platform operators or active tenant members to read a tenant."""
        if self.is_platform_operator(current_user):
            return
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if membership and membership.get("status") == _ACTIVE_STATUS:
            return
        raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "member")

    async def ensure_tenant_admin(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow platform admins or active tenant admins/owners to mutate a tenant."""
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
        """Allow self-service reads or platform admin access to user-centric resources."""
        if user_id == current_user.user_id or self.is_platform_admin(current_user):
            return
        raise TenantAccessDeniedError(str(current_user.user_id), str(user_id), "self_or_admin")
