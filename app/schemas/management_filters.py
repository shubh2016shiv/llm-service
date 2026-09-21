"""
Management list filters — the filter objects for list endpoints
=================================================================

What this file is for
---------------------
Management endpoints that return LISTS (e.g. "list all tenants") accept
optional filters — "only tenants with status active", "only members on
the enterprise tier", "only active deployments". Instead of passing a
handful of loose query-string values down through every layer, the route
handler bundles them into one small typed object here. The service and
persistence layers then receive ONE argument with a name, a type, and a
single place to live.

Every filter object is a frozen dataclass (explained below), so it is
cheap, readable, and cannot be accidentally changed mid-request.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string, so a hint can
# mention a class (like UUID) before it is imported. (Boilerplate.)
from __future__ import annotations

# The @dataclass decorator and its two switches, explained in plain words:
#
#   @dataclass               -> "Python, please write the boring methods
#                               for me": it auto-generates __init__ (the
#                               constructor), __eq__ (== comparison), and
#                               __repr__ (readable print), all from the
#                               field list below. No hand-written
#                               boilerplate.
#
#   frozen=True              -> "once built, do not let anyone change it".
#                               The object is immutable. Good for filter
#                               objects handed between layers, because no
#                               caller can silently mutate them.
#
#   slots=True               -> a memory optimization. Normally every
#                               object carries a small dict of attributes;
#                               slots stores the fields in compact fixed
#                               slots instead, so thousands of small
#                               filter objects cost less memory. (The
#                               trade-off: you can no longer attach a new
#                               attribute at runtime.)
from dataclasses import dataclass

# TYPE_CHECKING is only True while a type checker (mypy/pyright) reads the
# file, never at runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.schemas.auth_schema import TenantRole, UserRole
    from app.schemas.enums import (
        TenantLifecycleStatus,
        TenantSubscriptionTier,
        UserAccountStatus,
    )


@dataclass(frozen=True, slots=True)
class TenantListFilters:
    """The filters for "list tenants" / "count tenants".

    Both are optional; None means "no filtering on this dimension".
    """

    status_filter: TenantLifecycleStatus | None = None
    tier_filter: TenantSubscriptionTier | None = None


@dataclass(frozen=True, slots=True)
class TenantMembershipListFilters:
    """The filters for "list memberships" / "count memberships"."""

    tenant_role_filter: TenantRole | None = None
    active_only: bool = False  # True = keep only active memberships


@dataclass(frozen=True, slots=True)
class TenantDeploymentListFilters:
    """The filters for "list deployments" / "count deployments"."""

    provider_id: UUID | None = None  # keep only deployments on this provider
    active_only: bool = False  # True = keep only active deployments


@dataclass(frozen=True, slots=True)
class UserListFilters:
    """Validated platform-role and lifecycle filters for user pages."""

    platform_role: UserRole | None = None
    status: UserAccountStatus | None = None
