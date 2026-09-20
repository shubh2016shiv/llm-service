"""
Tenant access — the bouncer for management APIs
================================================

What this file is for
---------------------
Management APIs (create a tenant, list deployments, edit a user) must
answer one question first: "Is this caller allowed to touch THIS tenant's
data?" This file is that question, answered with two simple badges:

    1. Platform badges: some roles work building-wide. Platform admins
       may change anything; platform operators may read anything. These
       are checked first because they need no database lookup.
    2. Tenant membership: everyone else must be a member of the specific
       tenant being touched — and for changes, must hold an admin role
       INSIDE that tenant.

The three doors, in plain words
-------------------------------
    ensure_tenant_read    -> "may I LOOK at this tenant's data?"
    ensure_tenant_admin   -> "may I CHANGE this tenant's data?"
    ensure_self_or_admin  -> "may I touch this USER's own data?" (used by
                             profile-style routes; no database needed)

Every "no" raises one typed error (TenantAccessDeniedError) carrying the
specific reason, so the API layer can map denials to clean HTTP 403
responses.

Who uses this file
------------------
    Management API routes -> TenantAccessService -> TenantMembershipPersistence

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# TYPE_CHECKING is only True while a type checker reads the file, never at
# runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

# The one typed denial error every "no" in this file raises.
from app.core.exceptions import TenantAccessDeniedError

# The membership records reader (queries live in app/database).
from app.database import TenantMembershipPersistence

# The authenticated caller's shape, built by the JWT stage earlier.
from app.schemas.auth_schema import AuthTokenPayload

# The role lists that define the badges. They live here (one home) so a
# role's meaning is never re-invented per file:
#   PLATFORM_ADMIN_ROLES    = building-wide "may change anything" badge,
#   PLATFORM_OPERATOR_ROLES = building-wide "may read anything" badge,
#   TENANT_ADMIN_ROLES      = inside-one-tenant "may change" badge.
from app.schemas.role_hierarchy import (
    PLATFORM_ADMIN_ROLES,
    PLATFORM_OPERATOR_ROLES,
    TENANT_ADMIN_ROLES,
)

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from uuid import UUID

# The membership status that counts as "still a member". The membership
# reader hands back raw database rows, so this value is compared as text.
# tenant_inference_auth.py checks the same raw-row status text for the same
# reason, so it imports this one value instead of keeping its own copy.
ACTIVE_STATUS = "active"


class TenantAccessService:
    """The bouncer for tenant-scoped management permissions.

    Decision order, in plain words:
        1. Platform badge? Allow immediately — no database lookup.
        2. Otherwise, look up the caller's membership in the target
           tenant.
        3. For reads: an active membership is enough.
        4. For changes: the membership must also hold a tenant-admin
           role inside that tenant.
    """

    def __init__(self, membership_persistence: TenantMembershipPersistence) -> None:
        """Keep the membership records reader used for role lookups.

        Args:
            membership_persistence: The class that knows how to read one
                caller's membership record for one tenant.
        """
        self._memberships = membership_persistence

    def is_platform_admin(self, current_user: AuthTokenPayload) -> bool:
        """True when the caller holds a building-wide admin badge.

        These callers bypass every tenant check: an admin of the platform
        is implicitly trusted for all tenants.
        """
        # Is the caller's role on the short list of roles treated as
        # platform-admin-equivalent?
        return current_user.role in PLATFORM_ADMIN_ROLES

    def is_platform_operator(self, current_user: AuthTokenPayload) -> bool:
        """True when the caller holds a building-wide read badge.

        Operators may READ any tenant's data but cannot change it — that
        still requires an admin badge (see is_platform_admin).
        """
        return current_user.role in PLATFORM_OPERATOR_ROLES

    async def ensure_tenant_read(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow the caller to LOOK at one tenant's data, or raise.

        Allowed when either is true:
          - the caller has a platform operator-or-higher badge, or
          - the caller is an ACTIVE member of this specific tenant.

        Args:
            tenant_id: The tenant whose data is being read.
            current_user: The authenticated caller (JWT payload).

        Raises:
            TenantAccessDeniedError: When neither condition holds.
        """
        # Platform badge? Let them straight through — no database trip.
        if self.is_platform_operator(current_user):
            return
        # No badge: look up this caller's membership in THIS tenant.
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        # An active membership is enough for read-only access.
        if membership and membership.get("status") == ACTIVE_STATUS:
            return
        # Not a member (or the membership is not active) -> deny, with the
        # reason recorded for the API layer's 403 message.
        raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "member")

    async def ensure_tenant_admin(self, tenant_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow the caller to CHANGE one tenant's data, or raise.

        Stricter sibling of ensure_tenant_read, used before mutating
        operations (create/update/delete anything tenant-scoped).

        Allowed when:
          - the caller has a platform admin badge, or
          - the caller's membership exists, is active, AND holds a
            tenant-admin role inside this tenant.

        Args:
            tenant_id: The tenant whose data is being changed.
            current_user: The authenticated caller (JWT payload).

        Raises:
            TenantAccessDeniedError: With a reason telling WHICH
                requirement failed (no membership, not active, or not an
                admin).
        """
        # Platform admin badge? Straight through — no database trip.
        if self.is_platform_admin(current_user):
            return
        # No badge: look up this caller's membership in THIS tenant.
        membership = await self._memberships.get_membership(tenant_id, current_user.user_id)
        # Gate 1: a membership record must exist at all.
        if not membership:
            raise TenantAccessDeniedError(
                str(current_user.user_id), str(tenant_id), "tenant_membership"
            )
        # Gate 2: it must still be active (suspended/removed members cannot
        # change anything).
        if membership.get("status") != ACTIVE_STATUS:
            raise TenantAccessDeniedError(
                str(current_user.user_id), str(tenant_id), "active_membership"
            )
        # Gate 3: it must carry an admin-level role INSIDE the tenant.
        # (Being a plain member grants reads, never changes.)
        if membership.get("tenant_role") not in TENANT_ADMIN_ROLES:
            raise TenantAccessDeniedError(str(current_user.user_id), str(tenant_id), "admin")

    def ensure_self_or_admin(self, user_id: UUID, current_user: AuthTokenPayload) -> None:
        """Allow touching a user's own data — or any user's, for admins.

        Used on profile-style routes ("get/update my own account"), where
        the resource owner is a USER, not a tenant. Callers may only act
        on their own record — unless they hold a platform admin badge.

        This is the one method that needs no database: either the ID
        matches the caller's own ID, or the badge applies. Being async
        would be pure overhead, so it is deliberately plain/synchronous.

        Args:
            user_id: The user whose data is being touched.
            current_user: The authenticated caller (JWT payload).

        Raises:
            TenantAccessDeniedError: When the caller is neither the owner
                nor a platform admin.
        """
        # Owner by ID match, or platform admin badge? Allow.
        if user_id == current_user.user_id or self.is_platform_admin(current_user):
            return
        # Someone else's record without a badge -> deny.
        raise TenantAccessDeniedError(str(current_user.user_id), str(user_id), "self_or_admin")
