"""
Tenant Deployment Service
=========================

Business service for tenant deployment configuration management.

What is a deployment?
    A deployment maps a tenant-owned key (for example, ``my-gpt4``) to a
    provider/model combination and related runtime settings. Inference routing
    resolves incoming deployment keys through these records.

What this service adds beyond CRUD:
    - Enforces tenant-role authorization (read vs admin write access).
    - Validates referenced tenant/provider/model entities for create flows.
    - Normalizes persistence errors into domain exceptions.
    - Invalidates authorization and deployment-config caches after changes so
      routing decisions do not rely on stale state.

Enterprise Pattern: Authorization-Aware CRUD Service Pattern
    Mutating operations follow authorize, validate references, persist, and
    invalidate dependent caches.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

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
    from app.database import TenantDeploymentPersistence
    from app.schemas.auth_schema import AuthTokenPayload
    from app.schemas.management_filters import TenantDeploymentListFilters
    from app.schemas.management_schema import DeploymentCreateRequest, DeploymentUpdateRequest
    from app.services.management_reference_validation import (
        ManagementReferenceValidationService,
    )

logger = logging.getLogger(__name__)


class DeploymentCache(Protocol):
    """Protocol for cache operations required by deployment invalidation.

    A ``Protocol`` is a structural type contract: any object implementing the
    declared methods is accepted, even without explicit inheritance.
    """

    async def delete(self, key: str) -> bool:
        """Delete a cache key and return whether deletion was acknowledged."""
        ...

    async def publish(self, channel: str, message: str) -> bool:
        """Publish an invalidation message to subscribers and return success."""
        ...


class TenantDeploymentService:
    """Manage tenant deployment lifecycle and cache-coherence side effects."""

    def __init__(
        self,
        deployment_persistence: TenantDeploymentPersistence,
        access_service: TenantAccessService,
        reference_validation_service: ManagementReferenceValidationService,
        cache: DeploymentCache | None = None,
        authorization_cache: InferenceAuthorizationCache | None = None,
    ) -> None:
        """Initialize with persistence, authorization, and optional cache clients."""
        self._deployments = deployment_persistence
        self._access = access_service
        self._references = reference_validation_service
        self._cache = cache
        self._authorization_cache = authorization_cache

    async def create_deployment(
        self, tenant_id: UUID, request: DeploymentCreateRequest, current_user: AuthTokenPayload
    ) -> Row:
        """Create a deployment record for a tenant after admin authorization.

        The method validates foreign references first, then writes the record,
        then invalidates caches so the new deployment is routable immediately.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        await self._references.ensure_deployment_create_references(
            tenant_id=tenant_id,
            provider_id=request.provider_id,
            model_id=request.model_id,
        )
        try:
            row = await self._deployments.create_deployment(
                tenant_id=tenant_id,
                created_by_user_id=current_user.user_id,
                **request.model_dump(),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        await self._invalidate(tenant_id, request.deployment_key)
        return clean_row(row)

    async def list_deployments(
        self,
        tenant_id: UUID,
        current_user: AuthTokenPayload,
        filters: TenantDeploymentListFilters,
        limit: int,
        offset: int,
    ) -> list[Row]:
        """List tenant deployments after tenant-read authorization."""
        await self._access.ensure_tenant_read(tenant_id, current_user)
        rows = await self._deployments.list_deployments(tenant_id, filters, limit, offset)
        return clean_rows(rows)

    async def count_deployments(
        self,
        tenant_id: UUID,
        filters: TenantDeploymentListFilters,
    ) -> int:
        """Count deployments for pagination metadata with matching filters."""
        return await self._deployments.count_deployments(tenant_id, filters)

    async def get_deployment(
        self, tenant_id: UUID, deployment_id: UUID, current_user: AuthTokenPayload
    ) -> Row:
        """Retrieve one deployment after tenant-read authorization.

        The method confirms that the fetched deployment belongs to the
        requested tenant to prevent cross-tenant identifier access.
        """
        await self._access.ensure_tenant_read(tenant_id, current_user)
        row = await self._deployments.get_deployment_by_id(deployment_id)
        if row is None or str(row.get("tenant_id")) != str(tenant_id):
            raise ResourceNotFoundError("TenantDeployment", str(deployment_id))
        return clean_row(row)

    async def update_deployment(
        self,
        tenant_id: UUID,
        deployment_id: UUID,
        request: DeploymentUpdateRequest,
        current_user: AuthTokenPayload,
    ) -> Row:
        """Partially update deployment configuration after admin authorization.

        Updated values take effect promptly because both authorization and
        config caches are invalidated using the current deployment key.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        existing = await self.get_deployment(tenant_id, deployment_id, current_user)
        try:
            row = await self._deployments.update_deployment(
                deployment_id=deployment_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("TenantDeployment", str(deployment_id))
        await self._invalidate(tenant_id, str(existing.get("deployment_key")))
        return clean_row(row)

    async def activate_deployment(
        self, tenant_id: UUID, deployment_id: UUID, current_user: AuthTokenPayload
    ) -> Row:
        """Mark a deployment as active and invalidate dependent caches."""
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        existing = await self.get_deployment(tenant_id, deployment_id, current_user)
        row = await self._deployments.set_active(deployment_id)
        if row is None:
            raise ResourceNotFoundError("TenantDeployment", str(deployment_id))
        await self._invalidate(tenant_id, str(existing.get("deployment_key")))
        return clean_row(row)

    async def set_maintenance(
        self, tenant_id: UUID, deployment_id: UUID, current_user: AuthTokenPayload
    ) -> Row:
        """Set deployment status to maintenance mode and invalidate caches.

        "Maintenance mode" means routing should treat the deployment as
        temporarily unavailable without deleting the configuration.
        """
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        existing = await self.get_deployment(tenant_id, deployment_id, current_user)
        row = await self._deployments.set_maintenance(deployment_id)
        if row is None:
            raise ResourceNotFoundError("TenantDeployment", str(deployment_id))
        await self._invalidate(tenant_id, str(existing.get("deployment_key")))
        return clean_row(row)

    async def delete_deployment(
        self, tenant_id: UUID, deployment_id: UUID, current_user: AuthTokenPayload
    ) -> None:
        """Delete a deployment and immediately clear related cached routes."""
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        existing = await self.get_deployment(tenant_id, deployment_id, current_user)
        deleted = await self._deployments.delete_deployment(deployment_id)
        if not deleted:
            raise ResourceNotFoundError("TenantDeployment", str(deployment_id))
        await self._invalidate(tenant_id, str(existing.get("deployment_key")))

    async def _invalidate(self, tenant_id: UUID, deployment_key: str) -> None:
        """Invalidate cache layers affected by deployment state changes.

        Two cache scopes are refreshed:
            1. Authorization decisions for deployment-level inference access.
            2. Deployment configuration lookups used by routing components.
        """
        await self._invalidate_authorization_scope(tenant_id, deployment_key)
        await self._invalidate_deployment_config(tenant_id, deployment_key)

    async def _invalidate_authorization_scope(self, tenant_id: UUID, deployment_key: str) -> None:
        """Invalidate authorization cache entries for one deployment route."""
        if self._authorization_cache is None:
            return
        await self._authorization_cache.invalidate_deployment(tenant_id, deployment_key)

    async def _invalidate_deployment_config(self, tenant_id: UUID, deployment_key: str) -> None:
        """Invalidate deployment-config cache entries for one route key.

        The publish step supports distributed invalidation so multiple service
        instances can react to cache changes.
        """
        if self._cache is None:
            return
        cache_key = f"tenant:{tenant_id}:deployments:{deployment_key}"
        try:
            await self._cache.delete(cache_key)
            await self._cache.publish("deployment_config_invalidated", cache_key)
        except Exception:
            logger.warning("Deployment cache invalidation failed", exc_info=True)
