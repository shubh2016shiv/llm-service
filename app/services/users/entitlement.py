"""
User Entitlement Service
========================

Business service for managing user-specific routing overrides, called
entitlements.

What is an entitlement?
    An entitlement grants one user explicit access to one tenant deployment,
    optionally with user-scoped credentials. During inference routing, an
    active entitlement can override default tenant deployment behavior.

What this service adds beyond CRUD:
    - Enforces authorization based on caller role and identity.
    - Normalizes persistence validation failures into domain exceptions.
    - Invalidates route-specific authorization cache entries after changes so
      inference routing reflects entitlement updates immediately.

Enterprise Pattern: Authorization-Aware CRUD Service Pattern
    Mutating operations apply authorization, persist changes, and refresh the
    exact cache route impacted by the entitlement.

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
    from app.database import UserEntitlementPersistence
    from app.schemas.auth_schema import AuthTokenPayload
    from app.schemas.management_schema import EntitlementCreateRequest, EntitlementUpdateRequest


class UserEntitlementService:
    """Manage user-specific deployment entitlement records."""

    def __init__(
        self,
        entitlement_persistence: UserEntitlementPersistence,
        access_service: TenantAccessService,
        authorization_cache: InferenceAuthorizationCache | None = None,
    ) -> None:
        """Initialize with persistence adapter, access service, and optional cache."""
        self._entitlements = entitlement_persistence
        self._access = access_service
        self._authorization_cache = authorization_cache

    async def create_entitlement(
        self, user_id: UUID, request: EntitlementCreateRequest, current_user: AuthTokenPayload
    ) -> Row:
        """Create an entitlement linking a user to a tenant deployment.

        Caller must be tenant admin for the target tenant. On success, the
        route-specific authorization cache is invalidated for immediate effect.
        """
        await self._access.ensure_tenant_admin(request.tenant_id, current_user)
        try:
            row = await self._entitlements.create_entitlement(
                user_id=user_id,
                created_by_user_id=current_user.user_id,
                **request.model_dump(),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        await self._invalidate_entitlement_route(clean_row(row))
        return clean_row(row)

    async def list_user_entitlements(
        self,
        tenant_id: UUID,
        user_id: UUID,
        current_user: AuthTokenPayload,
        limit: int,
        offset: int,
    ) -> list[Row]:
        """List one user's entitlements within a tenant.

        Access requires both:
            - self-or-admin identity permission for the target user, and
            - tenant-read permission for the target tenant.
        """
        self._access.ensure_self_or_admin(user_id, current_user)
        await self._access.ensure_tenant_read(tenant_id, current_user)
        rows = await self._entitlements.get_user_entitlements(tenant_id, user_id, limit, offset)
        return clean_rows(rows)

    async def count_user_entitlements(
        self, tenant_id: UUID, user_id: UUID, current_user: AuthTokenPayload
    ) -> int:
        """Count entitlements for one user in one tenant with matching auth rules."""
        self._access.ensure_self_or_admin(user_id, current_user)
        await self._access.ensure_tenant_read(tenant_id, current_user)
        return await self._entitlements.count_user_entitlements(tenant_id, user_id)

    async def get_entitlement(
        self,
        user_id: UUID,
        entitlement_id: UUID,
        current_user: AuthTokenPayload,
    ) -> Row:
        """Retrieve a single entitlement after identity and tenant authorization.

        The method first confirms user ownership scope and then verifies
        tenant-read access for the entitlement's tenant.
        """
        self._access.ensure_self_or_admin(user_id, current_user)
        row = await self._entitlements.get_entitlement_by_id(entitlement_id)
        if row is None or str(row.get("user_id")) != str(user_id):
            raise ResourceNotFoundError("UserEntitlement", str(entitlement_id))
        await self._access.ensure_tenant_read(UUID(str(row["tenant_id"])), current_user)
        return clean_row(row)

    async def update_entitlement(
        self,
        user_id: UUID,
        entitlement_id: UUID,
        request: EntitlementUpdateRequest,
        current_user: AuthTokenPayload,
    ) -> Row:
        """Partially update an entitlement after tenant-admin authorization.

        Tenant admin check is evaluated against the entitlement's existing
        tenant association to prevent unauthorized cross-tenant updates.
        """
        existing = await self.get_entitlement(user_id, entitlement_id, current_user)
        tenant_id = UUID(str(existing["tenant_id"]))
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        try:
            row = await self._entitlements.update_entitlement(
                entitlement_id=entitlement_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("UserEntitlement", str(entitlement_id))
        await self._invalidate_entitlement_route(clean_row(row))
        return clean_row(row)

    async def delete_entitlement(
        self, user_id: UUID, entitlement_id: UUID, current_user: AuthTokenPayload
    ) -> None:
        """Delete an entitlement after tenant-admin authorization checks."""
        existing = await self.get_entitlement(user_id, entitlement_id, current_user)
        await self._access.ensure_tenant_admin(UUID(str(existing["tenant_id"])), current_user)
        deleted = await self._entitlements.delete_entitlement(entitlement_id)
        if not deleted:
            raise ResourceNotFoundError("UserEntitlement", str(entitlement_id))
        await self._invalidate_entitlement_route(existing)

    async def _invalidate_entitlement_route(self, entitlement: Row) -> None:
        """Invalidate cached authorization for one entitlement route tuple.

        The tuple is ``(tenant_id, user_id, deployment_key)`` which uniquely
        identifies routing decisions affected by entitlement updates.
        """
        if self._authorization_cache is None:
            return
        await self._authorization_cache.invalidate_route(
            tenant_id=UUID(str(entitlement["tenant_id"])),
            user_id=UUID(str(entitlement["user_id"])),
            deployment_key=str(entitlement["deployment_key"]),
        )
