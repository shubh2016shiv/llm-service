"""Unit tests for the centralized role privilege hierarchy."""

from __future__ import annotations

from typing import Literal, get_args

import pytest

from app.schemas.auth_schema import TenantRole, UserRole
from app.schemas.role_hierarchy import (
    ALL_PLATFORM_ROLES,
    ALL_TENANT_ROLES,
    PLATFORM_ADMIN_ROLES,
    PLATFORM_OPERATOR_ROLES,
    PLATFORM_ROLE_ORDER,
    TENANT_ADMIN_ROLES,
    TENANT_INFERENCE_ROLES,
    TENANT_ROLE_ORDER,
    _assert_hierarchy_covers_literal,
    platform_roles_at_or_above,
    tenant_roles_at_or_above,
)


def test_platform_hierarchy_matches_its_literal() -> None:
    """REQ: the ranked platform roles must cover exactly the UserRole Literal."""
    assert frozenset(get_args(UserRole)) == ALL_PLATFORM_ROLES


def test_tenant_hierarchy_matches_its_literal() -> None:
    """REQ: the ranked tenant roles must cover exactly the TenantRole Literal."""
    assert frozenset(get_args(TenantRole)) == ALL_TENANT_ROLES


def test_hierarchies_are_ordered_by_ascending_privilege() -> None:
    """REQ: the declared order must put the least-privileged role first."""
    assert PLATFORM_ROLE_ORDER[0] == "developer"
    assert PLATFORM_ROLE_ORDER[-1] == "owner"
    assert TENANT_ROLE_ORDER[0] == "viewer"
    assert TENANT_ROLE_ORDER[-1] == "owner"


def test_derived_sets_match_the_previously_hand_typed_values() -> None:
    """REQ: centralizing must not silently change who is authorized.

    These are the exact literal sets that were hand-typed in tenant_access.py
    and tenant_inference_auth.py before this module existed. Pinning them here
    proves the refactor was behavior-preserving.
    """
    assert frozenset({"admin", "owner"}) == PLATFORM_ADMIN_ROLES
    assert frozenset({"operator", "admin", "owner"}) == PLATFORM_OPERATOR_ROLES
    assert frozenset({"admin", "owner"}) == TENANT_ADMIN_ROLES
    assert frozenset({"developer", "operator", "admin", "owner"}) == TENANT_INFERENCE_ROLES


def test_viewer_is_excluded_from_inference_roles() -> None:
    """REQ: a tenant viewer is read-only and must never be inference-eligible."""
    assert "viewer" not in TENANT_INFERENCE_ROLES
    assert "viewer" in ALL_TENANT_ROLES


def test_platform_roles_at_or_above_includes_the_minimum_itself() -> None:
    """REQ: 'at or above' must be inclusive of the named role."""
    assert platform_roles_at_or_above("owner") == frozenset({"owner"})
    assert platform_roles_at_or_above("developer") == ALL_PLATFORM_ROLES


def test_tenant_roles_at_or_above_includes_the_minimum_itself() -> None:
    """REQ: 'at or above' must be inclusive for the tenant namespace too."""
    assert tenant_roles_at_or_above("owner") == frozenset({"owner"})
    assert tenant_roles_at_or_above("viewer") == ALL_TENANT_ROLES


def test_hierarchy_guard_rejects_a_role_declared_but_never_ranked() -> None:
    """REQ: adding a role to the Literal without ranking it must fail loudly."""
    unranked_literal = Literal["developer", "operator", "admin", "owner", "auditor"]

    with pytest.raises(ValueError, match=r"unranked.*auditor"):
        _assert_hierarchy_covers_literal(
            PLATFORM_ROLE_ORDER, unranked_literal, "PLATFORM_ROLE_ORDER"
        )


def test_hierarchy_guard_rejects_a_ranked_role_that_no_longer_exists() -> None:
    """REQ: removing a role from the Literal while it is still ranked must fail loudly."""
    shrunken_literal = Literal["developer", "operator"]

    with pytest.raises(ValueError, match=r"undeclared.*admin.*owner"):
        _assert_hierarchy_covers_literal(
            PLATFORM_ROLE_ORDER, shrunken_literal, "PLATFORM_ROLE_ORDER"
        )


def test_hierarchy_guard_rejects_a_duplicated_role() -> None:
    """REQ: a role ranked twice makes 'at or above' ambiguous and must fail loudly."""
    duplicated_order = ("developer", "operator", "admin", "owner", "owner")

    with pytest.raises(ValueError, match="more than once"):
        _assert_hierarchy_covers_literal(duplicated_order, UserRole, "PLATFORM_ROLE_ORDER")


# ---------------------------------------------------------------------------
# Every downstream consumer must agree with the single declaration.
#
# These pin the fix for the real defect: the role vocabulary used to be
# declared four separate times per namespace (auth Literal, management-schema
# Literal, persistence validator list, authorization frozenset) with nothing
# tying them together. A role added to one and not the others produced a
# persistence layer that accepts values authorization never grants, or an API
# surface that advertises roles the enforcement layer ignores.
# ---------------------------------------------------------------------------


def test_management_schema_roles_are_the_canonical_ones_not_copies() -> None:
    """REQ: the API request schemas must not re-declare the role vocabulary."""
    from app.schemas import management_schema

    assert management_schema.PlatformRole is UserRole
    assert management_schema.TenantRole is TenantRole


def test_platform_role_validator_list_matches_the_hierarchy() -> None:
    """REQ: persistence receives the exact role vocabulary authorization ranks."""
    from app.schemas.role_hierarchy import VALID_PLATFORM_ROLE_LIST

    assert frozenset(VALID_PLATFORM_ROLE_LIST) == ALL_PLATFORM_ROLES


def test_membership_persistence_tenant_roles_match_the_hierarchy() -> None:
    """REQ: what may be written to tenant_memberships.tenant_role must equal what auth ranks."""
    from app.schemas.role_hierarchy import VALID_TENANT_ROLE_LIST

    assert frozenset(VALID_TENANT_ROLE_LIST) == ALL_TENANT_ROLES


def test_platform_and_tenant_namespaces_are_not_interchangeable() -> None:
    """REQ: the two namespaces must stay distinct sets, not aliases of each other.

    They share four role names today, which is exactly why a set derived for
    one must never be reused to check the other. ``viewer`` is the role that
    proves they are different vocabularies.
    """
    assert ALL_PLATFORM_ROLES != ALL_TENANT_ROLES
    assert frozenset({"viewer"}) == ALL_TENANT_ROLES - ALL_PLATFORM_ROLES
