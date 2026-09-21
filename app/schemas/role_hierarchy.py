"""
Role Hierarchy
==============

The single place where "which roles exist" and "which roles outrank which" are
defined for this service.

Enterprise Pattern: Single Source of Truth + Fail-at-Startup Validation
    Every consumer used to hand-type its own copy of a role set --
    ``frozenset({"admin", "owner"})`` in an auth guard, ``["owner", "admin",
    "operator", "developer"]`` in a persistence validator, a re-declared
    ``Literal[...]`` in a request schema. Four independent declarations per
    namespace, with no mechanical link between them. Adding or renaming a role
    meant finding every copy and hoping none were missed, and a miss is not a
    crash -- it is a persistence validator that accepts a role the
    authorization layer will silently never grant, or vice versa.

    Here each namespace is declared exactly once, as an ordered tuple, and
    every set/list/Literal elsewhere is *derived* from it. The
    ``_assert_hierarchy_covers_literal`` calls below run at import time, so
    drift between the ordering and its ``Literal`` fails at process start
    rather than at request time -- the same fail-fast contract ``RoleGuard``
    already uses for its permitted role lists.

Why this lives in ``app.schemas`` and not ``app.auth``:
    Both ``app.auth`` (authorization decisions) and ``app.database``
    (persistence validators) must agree on the role vocabulary. Per this
    codebase's layering rule, a lower layer never imports from a higher one,
    and ``app.auth.authorization`` already imports ``app.database`` -- so auth
    sits above persistence and cannot be the shared source. ``app.schemas`` is
    below both and already owns the canonical ``UserRole``/``TenantRole``
    Literals, which makes it the only correct home.

Two separate namespaces, deliberately not merged:
    - Platform roles (``users.platform_role``): authority across the whole
      service. No ``viewer`` -- platform callers are always at least a
      developer.
    - Tenant roles (``tenant_memberships.tenant_role``): authority inside one
      tenant. Adds ``viewer``, a read-only observer who may list resources but
      may not invoke inference.

    The two happen to share four role *names*, which is exactly why they are
    kept apart: a set derived for one namespace must never be silently reused
    to check the other.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import get_args

from app.schemas.auth_schema import TenantRole, UserRole

# Ascending privilege. Position in the tuple IS the privilege level, so
# "at or above X" is a slice rather than a hand-maintained set.
PLATFORM_ROLE_ORDER: tuple[UserRole, ...] = ("developer", "operator", "admin", "owner")
TENANT_ROLE_ORDER: tuple[TenantRole, ...] = (
    "viewer",
    "developer",
    "operator",
    "admin",
    "owner",
)


def _assert_hierarchy_covers_literal(
    ordered_roles: tuple[str, ...],
    literal_type: object,
    hierarchy_name: str,
) -> None:
    """Fail at import if an ordered hierarchy and its Literal have drifted apart.

    Catches both directions of drift: a role added to the Literal but never
    ranked here, and a role ranked here that no longer exists in the Literal.
    """
    declared_roles = frozenset(get_args(literal_type))
    ranked_roles = frozenset(ordered_roles)
    if declared_roles != ranked_roles:
        unranked = sorted(declared_roles - ranked_roles)
        unknown = sorted(ranked_roles - declared_roles)
        raise ValueError(
            f"{hierarchy_name} is out of sync with its Literal. "
            f"Declared but unranked: {unranked}. Ranked but undeclared: {unknown}."
        )
    if len(ordered_roles) != len(ranked_roles):
        raise ValueError(f"{hierarchy_name} lists a role more than once: {ordered_roles}")


_assert_hierarchy_covers_literal(PLATFORM_ROLE_ORDER, UserRole, "PLATFORM_ROLE_ORDER")
_assert_hierarchy_covers_literal(TENANT_ROLE_ORDER, TenantRole, "TENANT_ROLE_ORDER")


def platform_roles_at_or_above(minimum_role: UserRole) -> frozenset[str]:
    """Return every platform role holding at least ``minimum_role``'s privilege."""
    return frozenset(PLATFORM_ROLE_ORDER[PLATFORM_ROLE_ORDER.index(minimum_role) :])


def tenant_roles_at_or_above(minimum_role: TenantRole) -> frozenset[str]:
    """Return every tenant role holding at least ``minimum_role``'s privilege."""
    return frozenset(TENANT_ROLE_ORDER[TENANT_ROLE_ORDER.index(minimum_role) :])


# Every role in a namespace, for guards that validate a role is known at all
# rather than comparing privilege (see RoleGuard and jwt_token_validator).
ALL_PLATFORM_ROLES: frozenset[str] = frozenset(PLATFORM_ROLE_ORDER)
ALL_TENANT_ROLES: frozenset[str] = frozenset(TENANT_ROLE_ORDER)

# Sorted list forms for persistence validators, whose `validate_enum_value`
# contract takes a list. Derived here so a persistence layer can never accept
# a role the authorization layer does not know about.
VALID_PLATFORM_ROLE_LIST: list[str] = sorted(ALL_PLATFORM_ROLES)
VALID_TENANT_ROLE_LIST: list[str] = sorted(ALL_TENANT_ROLES)

# Named sets the authorization services consume. Platform and tenant variants
# are separate names even where they hold equal values today, so a future
# change to one namespace cannot leak into the other.
PLATFORM_ADMIN_ROLES: frozenset[str] = platform_roles_at_or_above("admin")
PLATFORM_OPERATOR_ROLES: frozenset[str] = platform_roles_at_or_above("operator")
TENANT_ADMIN_ROLES: frozenset[str] = tenant_roles_at_or_above("admin")
# Inference requires at least developer: viewer is read-only and cannot invoke.
TENANT_INFERENCE_ROLES: frozenset[str] = tenant_roles_at_or_above("developer")
