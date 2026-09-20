"""
Inference authorization — the gatekeeper for inference requests
=================================================================

What this file is for
---------------------
Management APIs ask "may this caller touch this tenant's settings?".
Inference is the bigger question: "may THIS caller run THIS model, for
THIS tenant, under THIS deployment, right now?" This file answers that
question with a fixed, ordered sequence of checkpoints — like airport
security, each gate must pass before the next one is even attempted:

    1. Is the tenant real and active?          (otherwise: not found /
                                                suspended)
    2. Is the caller an active member with a   (otherwise: access denied)
       role that may run inference?
    3. Does the deployment exist and is it     (otherwise: not found /
       active?                                  inactive)
    4. Is there an active entitlement for      (otherwise: access denied)
       this exact provider/model route?

When ALL gates pass, the method builds one frozen "pass" — the
InferenceAccessContext — carrying every identifier the downstream routing
and provider code needs, so nothing has to re-verify anything.

The cache plays first
---------------------
Before any database work, one quick question: "did we already answer
'yes' for this exact caller+tenant+deployment recently?" If so, the saved
answer is returned instantly. The cache's own job — keeping that saved
"yes" honest — lives in authorization_grant_cache.py.

Naming warning (read this twice)
--------------------------------
This class (InferenceAuthorizationService) and TenantAccessService in
tenant_access.py sound interchangeable but are NOT:

    TenantAccessService        = management APIs (users, tenants,
                                 deployments, entitlements).
    InferenceAuthorizationService = inference requests only.

Tell them apart by which file they live in, not by their names.

Who uses this file
------------------
    Inference API dependency -> InferenceAuthorizationService
         +--> cache lookup (fast path)
         +--> tenant -> membership -> deployment -> entitlement checks
         +--> build InferenceAccessContext
         |
         v
    app.services inference execution

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# cast = tell the type checker "trust me on this one" (used exactly once,
# for the tenant role, where the value was just proven valid).
from typing import cast

# UUID = the globally unique id type used by every record in this project.
from uuid import UUID

# The rememberer: checks and fills the saved-answer cache.
from app.auth.authorization.authorization_grant_cache import AuthorizationGrantCache

# The "still a member" status text — the single source of truth for this
# literal, shared with tenant_access.py so the two files can never disagree
# on what "active" means for a raw membership row.
from app.auth.authorization.tenant_access import ACTIVE_STATUS

# The typed failures this file raises, one per failed gate:
from app.core.exceptions import (
    DeploymentInactiveError,
    DeploymentNotFoundError,
    TenantAccessDeniedError,
    TenantNotFoundError,
    TenantSuspendedError,
)

# The four source-of-truth readers (queries live in app/database).
from app.database import (
    TenantDeploymentPersistence,
    TenantMembershipPersistence,
    TenantPersistence,
    UserEntitlementPersistence,
)

# The caller's shape and the final "pass" object this file builds.
from app.schemas.auth_schema import AuthTokenPayload, InferenceAccessContext, TenantRole

# The list of tenant roles allowed to run inference.
from app.schemas.role_hierarchy import TENANT_INFERENCE_ROLES

# Tenant statuses that may receive inference traffic: "active" or "trial".
_TENANT_ACTIVE_STATUSES: frozenset[str] = frozenset({"active", "trial"})


class InferenceAuthorizationService:
    """Authorize one inference route against the source of truth.

    Deliberately strict: ANY failed checkpoint raises a typed error that
    the API layer can map to a clean HTTP response. There is no partial
    pass — either every gate opens, or nothing runs.
    """

    def __init__(
        self,
        tenant_persistence: TenantPersistence,
        membership_persistence: TenantMembershipPersistence,
        deployment_persistence: TenantDeploymentPersistence,
        entitlement_persistence: UserEntitlementPersistence,
        authorization_cache: AuthorizationGrantCache,
    ) -> None:
        """Keep the four source-of-truth readers and the saved-answer cache.

        One dependency per data concern: tenant, membership, deployment,
        entitlement, plus the cache. This service only ORCHESTRATES them —
        it owns no queries itself.
        """
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
        """Answer "may this caller run this deployment?" — with caching.

        Fast path: a remembered "yes" for this exact caller/tenant/
        deployment is returned immediately, no database work at all.
        Slow path: run the full checkpoint sequence, then remember the
        answer for next time — but ONLY if nothing changed while the
        checks were running (the cache verifies that).

        Args:
            tenant_id: The tenant scope from the request header.
            deployment_key: Which deployment route within that tenant.
            current_user: The authenticated caller (JWT payload).

        Returns:
            The frozen "pass" with every identifier downstream needs.

        Raises:
            TenantAccessDeniedError: Membership/role/entitlement gate
                failed.
            DeploymentNotFoundError: No such deployment key.
            DeploymentInactiveError: The deployment is not active.
        """
        # Checkpoint 0 (the fast path): did we already say "yes"?
        # find_grant also returns the change stamps it observed, which the
        # store step below will re-verify.
        grant_lookup = await self._cache.find_grant(
            tenant_id,
            current_user.user_id,
            deployment_key,
        )
        if grant_lookup.context is not None:
            # A valid remembered "yes" — hand over the saved pass.
            return grant_lookup.context

        # No usable memory of a "yes": run the full checkpoint sequence.
        context = await self._authorize_from_source_of_truth(
            tenant_id,
            deployment_key,
            current_user,
        )
        # Remember the answer for next time — but only if the world has
        # not changed while we were checking (the cache re-verifies the
        # stamps we observed at lookup time).
        if grant_lookup.observed_versions is not None:
            await self._cache.store_grant_if_unchanged(context, grant_lookup.observed_versions)
        return context

    async def _authorize_from_source_of_truth(
        self,
        tenant_id: UUID,
        deployment_key: str,
        current_user: AuthTokenPayload,
    ) -> InferenceAccessContext:
        """Run the four checkpoints against PostgreSQL, in strict order.

        Each gate is checked only after the previous one passed, and each
        failure raises immediately — the first broken gate is the one the
        caller hears about.
        """
        # Gate 1: does the tenant exist, and may it receive traffic?
        tenant = await self._tenants.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        tenant_status = str(tenant.get("status", ""))
        if tenant_status not in _TENANT_ACTIVE_STATUSES:
            raise TenantSuspendedError(str(tenant_id), reason=f"status={tenant_status}")

        # Gate 2: is the caller an active member with an inference role?
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        if membership is None or membership.get("status") != ACTIVE_STATUS:
            # Either no membership record at all, or it is not active.
            raise TenantAccessDeniedError(
                str(current_user.user_id), str(tenant_id), "active_member"
            )
        tenant_role = str(membership.get("tenant_role", ""))
        if tenant_role not in TENANT_INFERENCE_ROLES:
            # A member, but not one allowed to run inference.
            raise TenantAccessDeniedError(
                str(current_user.user_id), str(tenant_id), "inference_eligible_role"
            )

        # Gate 3: does the deployment exist, and is it active?
        deployment = await self._deployments.get_deployment_by_key(tenant_id, deployment_key)
        if deployment is None:
            raise DeploymentNotFoundError(str(tenant_id), deployment_key)
        deployment_status = str(deployment.get("status", ""))
        if deployment_status != ACTIVE_STATUS:
            raise DeploymentInactiveError(deployment_key, deployment_status)

        # The deployment row names its provider and model by UUID; read
        # them once here because the next gate needs them.
        provider_id = UUID(str(deployment["provider_id"]))
        model_id = UUID(str(deployment["model_id"]))

        # Gate 4: is there an active entitlement for THIS exact route
        # (tenant + user + deployment + provider + model)?
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

        # All four gates opened. Build the frozen pass.
        return InferenceAccessContext(
            tenant_id=tenant_id,
            user_id=current_user.user_id,
            deployment_key=deployment_key,
            deployment_id=UUID(str(deployment["deployment_id"])),
            provider_id=provider_id,
            model_id=model_id,
            # The role string was just proven to be an inference role; the
            # cast tells the type checker we verified it.
            tenant_role=cast("TenantRole", tenant_role),
            entitlement_id=UUID(str(entitlement["entitlement_id"])),
        )
