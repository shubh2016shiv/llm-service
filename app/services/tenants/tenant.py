"""
Tenant Service
==============

Business service for managing tenant accounts, which act as organizational
boundaries for deployments, memberships, and quota policies.

What is a tenant?
    A tenant represents one isolated organization or team in the platform.
    Isolation means data and runtime permissions are scoped per tenant so one
    tenant cannot inspect or invoke another tenant's resources.

What this service adds beyond plain CRUD:
    - Enforces tenant-level authorization for read and write operations.
    - Normalizes persistence errors into domain errors.
    - Sanitizes returned rows to avoid leaking sensitive fields.
    - Invalidates tenant-scoped authorization cache entries after status or
      lifecycle changes so inference decisions reflect the newest state.

Enterprise Pattern: Authorization-Aware CRUD Service Pattern
    Mutating paths follow a stable sequence: authorize, validate/persist, and
    invalidate dependent cache state.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ResourceNotFoundError
from app.services.management_helpers import (
    Row,
    clean_row,
    clean_rows,
    raise_clean_validation_error,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.auth.authorization.cache import InferenceAuthorizationCache
    from app.auth.authorization.tenant_access import TenantAccessService
    from app.database import TenantPersistence
    from app.schemas.auth_schema import AuthTokenPayload
    from app.schemas.management_filters import TenantListFilters
    from app.schemas.management_schema import TenantCreateRequest, TenantUpdateRequest


class TenantService:
    """Manage tenant account lifecycle and tenant-scope policy boundaries."""

    def __init__(
        self,
        tenant_persistence: TenantPersistence,
        access_service: TenantAccessService,
        authorization_cache: InferenceAuthorizationCache | None = None,
    ) -> None:
        """Initialize with persistence, access control, and optional cache."""
        self._tenants = tenant_persistence
        self._access = access_service
        self._authorization_cache = authorization_cache

    async def create_tenant(self, request: TenantCreateRequest) -> Row:
        """Create a new tenant record.

        Tenant-scoped authorization is not performed here because the tenant
        does not exist yet. Platform-level authorization should already be
        enforced at the route layer (for example, an admin-only guard).
        """
        try:
            row = await self._tenants.create_tenant(**request.model_dump())
            return clean_row(row)
        except ValueError as exc:
            raise_clean_validation_error(exc)

    async def list_tenants(
        self,
        filters: TenantListFilters,
        limit: int,
        offset: int,
    ) -> list[Row]:
        """List tenants using provided filters and pagination controls.

        Args:
            filters: Structured filter set for status, name, or other fields.
            limit: Maximum tenants to return in a single page.
            offset: Number of tenants skipped before page retrieval.
        """
        rows = await self._tenants.list_tenants(filters, limit, offset)
        return clean_rows(rows)

    async def count_tenants(self, filters: TenantListFilters) -> int:
        """Return tenant count for the same filter set used by list endpoints."""
        return await self._tenants.count_tenants(filters)

    async def get_tenant(self, tenant_id: UUID, current_user: AuthTokenPayload) -> Row:
        """Retrieve one tenant after tenant-read authorization checks."""
        await self._access.ensure_tenant_read(tenant_id, current_user)
        row = await self._tenants.get_tenant_by_id(tenant_id)
        if row is None:
            raise ResourceNotFoundError("Tenant", str(tenant_id))
        return clean_row(row)

    async def update_tenant(
        self, tenant_id: UUID, request: TenantUpdateRequest, current_user: AuthTokenPayload
    ) -> Row:
        """Partially update tenant metadata after tenant-admin authorization.

        If the request changes tenant status, tenant-scoped authorization cache
        entries are invalidated so inference requests see the new status
        immediately.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        try:
            row = await self._tenants.update_tenant(
                tenant_id=tenant_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("Tenant", str(tenant_id))
        if request.status is not None:
            await self._invalidate_tenant_scope(tenant_id)
        return clean_row(row)

    async def suspend_tenant(self, tenant_id: UUID, current_user: AuthTokenPayload) -> Row:
        """Suspend a tenant so inference access is effectively paused.

        Suspension affects authorization outcomes. Cache invalidation ensures
        a previously cached "allow" decision is not reused after suspension.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        row = await self._tenants.suspend_tenant(tenant_id)
        if row is None:
            raise ResourceNotFoundError("Tenant", str(tenant_id))
        await self._invalidate_tenant_scope(tenant_id)
        return clean_row(row)

    async def activate_tenant(self, tenant_id: UUID, current_user: AuthTokenPayload) -> Row:
        """Activate a tenant and refresh tenant-scoped authorization cache state."""
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        row = await self._tenants.activate_tenant(tenant_id)
        if row is None:
            raise ResourceNotFoundError("Tenant", str(tenant_id))
        await self._invalidate_tenant_scope(tenant_id)
        return clean_row(row)

    async def delete_tenant(self, tenant_id: UUID) -> None:
        """Delete a tenant and invalidate all tenant-scoped cached grants.

        Route-level policy is expected to enforce platform-owner permissions
        before this method is called.
        """
        deleted = await self._tenants.delete_tenant(tenant_id)
        if not deleted:
            raise ResourceNotFoundError("Tenant", str(tenant_id))
        await self._invalidate_tenant_scope(tenant_id)

    async def _invalidate_tenant_scope(self, tenant_id: UUID) -> None:
        """Invalidate authorization cache entries linked to one tenant.

        This helper is a no-op when cache infrastructure is not injected,
        which keeps local/test execution lightweight.
        """
        if self._authorization_cache is None:
            return
        await self._authorization_cache.invalidate_tenant(tenant_id)
