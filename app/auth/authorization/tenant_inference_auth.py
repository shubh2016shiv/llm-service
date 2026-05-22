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

Step-by-step decision sequence:
    1. Check route-level cache entry and version snapshot.
    2. Validate tenant existence and active status.
    3. Validate caller membership and tenant role suitability for inference.
    4. Validate deployment existence and active status.
    5. Validate active entitlement for this exact provider/model route.
    6. Build ``InferenceAccessContext`` for downstream execution.
    7. Cache the result only if version markers are still unchanged.

Dependencies:
    - app.database: Source-of-truth authorization lookups.

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
    """Authorize tenant-scoped inference routes against source-of-truth state.

    This service is intentionally strict: any failed prerequisite raises a
    typed denial/error that can be mapped cleanly at API boundaries.
    """

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
            ``InferenceAccessContext`` containing all identifiers required by
            downstream routing and provider execution.

        Raises:
            TenantAccessDeniedError: If membership or entitlement is missing.
            DeploymentNotFoundError: If the deployment key does not exist.
            DeploymentInactiveError: If the deployment is not active.
        """
        # 1) Attempt cache fast-path with version-snapshot freshness checks.
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

        # 2) Tenant-level validation.
        tenant = await self._tenants.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        tenant_status = str(tenant.get("status", ""))
        if tenant_status not in _TENANT_ACTIVE_STATUSES:
            raise TenantSuspendedError(str(tenant_id), reason=f"status={tenant_status}")

        # 3) Membership and tenant-role validation.
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if membership is None or membership.get("status") != _ACTIVE_STATUS:
            raise TenantAccessDeniedError(
                str(current_user.user_id), str(tenant_id), "active_member"
            )

        tenant_role = str(membership.get("tenant_role", ""))
        if tenant_role not in _INFERENCE_ROLES:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "developer")

        # 4) Deployment-route validation.
        deployment = await self._deployments.get_deployment_by_key(tenant_id, deployment_key)
        if deployment is None:
            raise DeploymentNotFoundError(str(tenant_id), deployment_key)
        deployment_status = str(deployment.get("status", ""))
        if deployment_status != _ACTIVE_STATUS:
            raise DeploymentInactiveError(deployment_key, deployment_status)

        provider_id = UUID(str(deployment["provider_id"]))
        model_id = UUID(str(deployment["model_id"]))
        # 5) Entitlement validation for the resolved provider/model route.
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

        # 6) Build typed context used by downstream inference execution.
        context = InferenceAccessContext(
            tenant_id=tenant_id,
            user_id=current_user.user_id,
            deployment_key=deployment_key,
            deployment_id=UUID(str(deployment["deployment_id"])),
            provider_id=provider_id,
            model_id=model_id,
            # Why cast(TenantRole, ...) is required here:
            #
            # `tenant_role` is extracted from a database row as a plain Python str.
            # The database layer has no knowledge of our TenantRole enum, so the
            # type checker correctly considers it to be str at this point.
            #
            # TenantRole is a typed string enum — it represents only the specific
            # role values this system recognises ("developer", "operator", "admin",
            # "owner"). The guard two lines above (`if tenant_role not in
            # _INFERENCE_ROLES: raise TenantAccessDeniedError`) has already
            # verified at runtime that the value is one of those valid roles.
            # If it were not, an exception would have been raised and execution
            # would never reach this line.
            #
            # The issue is that Python's type checker does not understand that a
            # frozenset membership check narrows the type. After the guard, the
            # type checker still sees `tenant_role` as a plain str, not as
            # TenantRole — so passing it where TenantRole is expected produces
            # a type error. cast(TenantRole, tenant_role) is our explicit
            # instruction to the type checker: "we have already verified this
            # value at runtime; treat it as TenantRole from here on."
            # At runtime, cast() is a no-op — it returns its argument unchanged
            # with zero conversion or checking.
            tenant_role=cast("TenantRole", tenant_role),
            entitlement_id=UUID(str(entitlement["entitlement_id"])),
        )
        # 7) Cache result only when versions stayed stable during computation.
        final_snapshot = await self._cache.read_version_snapshot(
            tenant_id,
            current_user.user_id,
            deployment_key,
        )
        if current_snapshot is not None and current_snapshot == final_snapshot:
            await self._cache.set(context, current_snapshot)
        return context
