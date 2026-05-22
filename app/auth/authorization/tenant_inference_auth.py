"""
Tenant Authorization Service
============================

This module decides whether a caller can run inference on a specific tenant
and deployment key.

Enterprise Pattern: Authorization Service Orchestration Pattern
    `TenantAuthorizationService` orchestrates focused persistence adapters
    (tenant, membership, deployment, entitlement) plus cache. Each adapter
    handles one data concern; this service combines their results into one
    allow/deny decision.

How the flow works:
    Inference API dependency
        |
        v
    TenantAuthorizationService
        +--> Check cache snapshot and cached grant
        +--> Validate tenant is present and active
        +--> Validate membership is active with allowed role
        +--> Validate deployment exists and is active
        +--> Validate entitlement exists for provider/model route
        +--> Build InferenceAccessContext
        |
        v
    app.services inference execution

Dependencies:
    - app.database: Source-of-truth authorization lookups.
    - app.schemas.auth_schema: Identity payload and safe auth context.
    - app.core.exceptions: Stable domain errors returned to API layer.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from app.auth.authorization.cache import InferenceAuthorizationCache
from app.core.exceptions import (
    DeploymentInactiveError,
    DeploymentNotFoundError,
    TenantAccessDeniedError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.database import (
    TenantDeploymentPersistence,
    TenantMembershipPersistence,
    TenantPersistence,
    UserEntitlementPersistence,
)
from app.schemas.auth_schema import AuthTokenPayload, InferenceAccessContext, TenantRole

_ACTIVE_STATUS = "active"
_TENANT_ACTIVE_STATUSES: frozenset[str] = frozenset({"active", "trial"})
_INFERENCE_ROLES: frozenset[str] = frozenset({"developer", "operator", "admin", "owner"})


class TenantAuthorizationService:
    """Authorize tenant-specific inference requests against PostgreSQL state."""

    def __init__(
        self,
        tenant_persistence: TenantPersistence,
        membership_persistence: TenantMembershipPersistence,
        deployment_persistence: TenantDeploymentPersistence,
        entitlement_persistence: UserEntitlementPersistence,
        authorization_cache: InferenceAuthorizationCache,
    ) -> None:
        """Initialize with persistence dependencies and cache."""
        self._tenants = tenant_persistence
        self._memberships = membership_persistence
        self._deployments = deployment_persistence
        self._entitlements = entitlement_persistence
        self._cache = authorization_cache

    async def authorize_inference(
        self,
        tenant_id: UUID,
        deployment_key: str,
        current_user: AuthTokenPayload,
    ) -> InferenceAccessContext:
        """Authorize one user to invoke one tenant deployment.

        Args:
            tenant_id: Tenant scope supplied by the request header.
            deployment_key: Tenant-scoped deployment route key.
            current_user: Authenticated JWT payload.

        Returns:
            Safe authorization context for downstream inference.

        Raises:
            TenantAccessDeniedError: If membership or entitlement is missing.
            DeploymentNotFoundError: If the deployment key does not exist.
            DeploymentInactiveError: If the deployment is not active.
        """
        current_snapshot = await self._cache.read_version_snapshot(
            tenant_id,
            current_user.user_id,
            deployment_key,
        )
        cached_entry = await self._cache.get_entry(tenant_id, current_user.user_id, deployment_key)
        if cached_entry is not None and current_snapshot is not None:
            if cached_entry.version_snapshot == current_snapshot:
                return cached_entry.context
            await self._cache.delete_route_grant(tenant_id, current_user.user_id, deployment_key)

        tenant = await self._tenants.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        tenant_status = str(tenant.get("status", ""))
        if tenant_status not in _TENANT_ACTIVE_STATUSES:
            raise TenantSuspendedError(str(tenant_id), reason=f"status={tenant_status}")

        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if membership is None or membership.get("status") != _ACTIVE_STATUS:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "active_member")

        tenant_role = str(membership.get("tenant_role", ""))
        if tenant_role not in _INFERENCE_ROLES:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "developer")

        deployment = await self._deployments.get_deployment_by_key(tenant_id, deployment_key)
        if deployment is None:
            raise DeploymentNotFoundError(str(tenant_id), deployment_key)
        deployment_status = str(deployment.get("status", ""))
        if deployment_status != _ACTIVE_STATUS:
            raise DeploymentInactiveError(deployment_key, deployment_status)

        provider_id = UUID(str(deployment["provider_id"]))
        model_id = UUID(str(deployment["model_id"]))
        entitlement = await self._entitlements.get_active_entitlement_for_route(
            tenant_id=tenant_id,
            user_id=current_user.user_id,
            deployment_key=deployment_key,
            provider_id=provider_id,
            model_id=model_id,
        )
        if entitlement is None:
            raise TenantAccessDeniedError(
                str(current_user.user_id), str(tenant_id), "active_entitlement"
            )

        context = InferenceAccessContext(
            tenant_id=tenant_id,
            user_id=current_user.user_id,
            deployment_key=deployment_key,
            deployment_id=UUID(str(deployment["deployment_id"])),
            provider_id=provider_id,
            model_id=model_id,
            tenant_role=cast("TenantRole", tenant_role),
            entitlement_id=UUID(str(entitlement["entitlement_id"])),
        )
        final_snapshot = await self._cache.read_version_snapshot(
            tenant_id,
            current_user.user_id,
            deployment_key,
        )
        if current_snapshot is not None and current_snapshot == final_snapshot:
            await self._cache.set(context, current_snapshot)
        return context

