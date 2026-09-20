"""Unit tests for repository filter validation before database access.

Architecture:
    Management filters -> repository validation -> query builder -> database
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.database.tenant_memberships import TenantMembershipPersistence
from app.database.tenants import TenantPersistence
from app.database.users import UserPersistence
from app.schemas.management_filters import TenantListFilters, TenantMembershipListFilters


@pytest.mark.asyncio
async def test_list_tenants_with_empty_status_filter_raises_value_error() -> None:
    """REQ: an explicitly supplied empty status cannot bypass validation."""
    persistence = object.__new__(TenantPersistence)
    filters = TenantListFilters(status_filter="")

    with pytest.raises(ValueError, match="status_filter"):
        await persistence.list_tenants(filters)


@pytest.mark.asyncio
async def test_count_tenants_with_empty_tier_filter_raises_value_error() -> None:
    """REQ: list and count reject the same malformed tenant tier filter."""
    persistence = object.__new__(TenantPersistence)
    filters = TenantListFilters(tier_filter="")

    with pytest.raises(ValueError, match="tier_filter"):
        await persistence.count_tenants(filters)


@pytest.mark.asyncio
async def test_list_memberships_with_empty_role_filter_raises_value_error() -> None:
    """REQ: an explicitly supplied empty tenant role is invalid."""
    persistence = object.__new__(TenantMembershipPersistence)
    filters = TenantMembershipListFilters(tenant_role_filter="")

    with pytest.raises(ValueError, match="tenant_role_filter"):
        await persistence.list_tenant_memberships(
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            filters=filters,
        )


@pytest.mark.asyncio
async def test_list_users_with_empty_status_filter_raises_value_error() -> None:
    """REQ: an explicitly supplied empty user status is invalid."""
    persistence = object.__new__(UserPersistence)

    with pytest.raises(ValueError, match="status_filter"):
        await persistence.get_all_users(status_filter="")
