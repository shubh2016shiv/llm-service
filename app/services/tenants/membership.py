"""
Tenant Membership Service
=========================

Business service for managing tenant membership records and tenant roles.

What is a membership?
    A membership links one user to one tenant with an assigned role. Roles
    control tenant-level privileges such as administration, deployment usage,
    or read-only visibility.

What this service adds beyond CRUD:
    - Enforces role-aware authorization (member for read, admin for writes).
    - Verifies referenced entities before write operations.
    - Normalizes persistence errors into typed domain exceptions.
    - Invalidates tenant/user authorization cache entries after membership
      changes so permission decisions are refreshed immediately.

Enterprise Pattern: Authorization-Aware CRUD Service Pattern
    Operations are organized as authorize, validate references, persist, and
    invalidate affected cache scope.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import ResourceNotFoundError
from app.services.management_helpers import (
    Row,
    clean_row,
    clean_rows,
    raise_clean_validation_error,
)

if TYPE_CHECKING:
    from app.auth.authorization.cache import InferenceAuthorizationCache
    from app.auth.authorization.tenant_access import TenantAccessService
    from app.database import TenantMembershipPersistence
    from app.schemas.auth_schema import AuthTokenPayload
    from app.schemas.management_filters import TenantMembershipListFilters
    from app.schemas.management_schema import MembershipCreateRequest, MembershipUpdateRequest
    from app.services.management_reference_validation import (
        ManagementReferenceValidationService,
    )


class TenantMembershipService:
    """Manage user-to-tenant role assignments and their side effects."""

    def __init__(
        self,
        membership_persistence: TenantMembershipPersistence,
        access_service: TenantAccessService,
        reference_validation_service: ManagementReferenceValidationService,
        authorization_cache: InferenceAuthorizationCache | None = None,
    ) -> None:
        """Initialize with persistence, access checks, and optional cache."""
        self._memberships = membership_persistence
        self._access = access_service
        self._references = reference_validation_service
        self._authorization_cache = authorization_cache

    async def create_membership(
        self, tenant_id: UUID, request: MembershipCreateRequest, current_user: AuthTokenPayload
    ) -> Row:
        """Create a tenant membership for a user.

        Flow:
            1. Require tenant-admin rights for the caller.
            2. Validate tenant and user references.
            3. Persist membership record.
            4. Invalidate cached authorization for affected tenant/user scope.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        await self._references.ensure_membership_create_references(tenant_id, request.user_id)
        try:
            row = await self._memberships.create_membership(
                tenant_id=tenant_id,
                created_by_user_id=current_user.user_id,
                **request.model_dump(),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        await self._invalidate_membership_scope(tenant_id, UUID(str(row["user_id"])))
        return clean_row(row)

    async def list_tenant_memberships(
        self,
        tenant_id: UUID,
        current_user: AuthTokenPayload,
        filters: TenantMembershipListFilters,
        limit: int,
        offset: int,
    ) -> list[Row]:
        """List memberships for one tenant after tenant-read authorization."""
        await self._access.ensure_tenant_read(tenant_id, current_user)
        rows = await self._memberships.list_tenant_memberships(tenant_id, filters, limit, offset)
        return clean_rows(rows)

    async def count_tenant_members(
        self,
        tenant_id: UUID,
        filters: TenantMembershipListFilters,
    ) -> int:
        """Count tenant memberships matching the supplied filter set."""
        return await self._memberships.count_tenant_members(tenant_id, filters)

    async def get_tenant_membership(
        self, tenant_id: UUID, membership_id: UUID, current_user: AuthTokenPayload
    ) -> Row:
        """Retrieve one membership after tenant-read authorization.

        The membership is additionally checked for tenant ownership so a valid
        membership ID from another tenant cannot be accessed through this path.
        """
        await self._access.ensure_tenant_read(tenant_id, current_user)
        row = await self._memberships.get_membership_by_id(membership_id)
        if row is None or str(row.get("tenant_id")) != str(tenant_id):
            raise ResourceNotFoundError("TenantMembership", str(membership_id))
        return clean_row(row)

    async def update_membership(
        self,
        tenant_id: UUID,
        membership_id: UUID,
        request: MembershipUpdateRequest,
        current_user: AuthTokenPayload,
    ) -> Row:
        """Partially update a membership after tenant-admin authorization.

        Cache invalidation is based on the existing user association so role
        changes are reflected in subsequent authorization decisions.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        existing = await self.get_tenant_membership(tenant_id, membership_id, current_user)
        try:
            row = await self._memberships.update_membership(
                membership_id=membership_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("TenantMembership", str(membership_id))
        await self._invalidate_membership_scope(tenant_id, UUID(str(existing["user_id"])))
        return clean_row(row)

    async def delete_membership(
        self, tenant_id: UUID, membership_id: UUID, current_user: AuthTokenPayload
    ) -> None:
        """Delete a membership after tenant-admin authorization checks."""
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        existing = await self.get_tenant_membership(tenant_id, membership_id, current_user)
        deleted = await self._memberships.delete_membership_by_id(membership_id)
        if not deleted:
            raise ResourceNotFoundError("TenantMembership", str(membership_id))
        await self._invalidate_membership_scope(tenant_id, UUID(str(existing["user_id"])))

    async def list_user_memberships(
        self, user_id: UUID, current_user: AuthTokenPayload, limit: int, offset: int
    ) -> list[Row]:
        """List memberships for one user with self-or-admin authorization."""
        self._access.ensure_self_or_admin(user_id, current_user)
        rows = await self._memberships.list_user_memberships(user_id, limit, offset)
        return clean_rows(rows)

    async def count_user_tenants(self, user_id: UUID, current_user: AuthTokenPayload) -> int:
        """Count how many tenants a user belongs to.

        Access is restricted to the user themselves or a platform admin.
        """
        self._access.ensure_self_or_admin(user_id, current_user)
        return await self._memberships.count_user_tenants(user_id)

    async def _invalidate_membership_scope(self, tenant_id: UUID, user_id: UUID) -> None:
        """Invalidate cached authorization decisions for one tenant/user pair."""
        if self._authorization_cache is None:
            return
        await self._authorization_cache.invalidate_membership(tenant_id, user_id)
