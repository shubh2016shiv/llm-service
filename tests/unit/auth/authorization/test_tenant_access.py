"""Unit tests for tenant-scoped management access policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.auth.authorization.tenant_access import TenantAccessService
from app.core.exceptions import TenantAccessDeniedError
from app.schemas.auth_schema import AuthTokenPayload, UserRole

TENANT_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
CALLER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("cccccccc-0000-0000-0000-000000000001")


class FakeTenantMembershipPersistence:
    """Return one controlled membership row without database access."""

    def __init__(self, membership: dict[str, str] | None) -> None:
        self.membership = membership
        self.calls: list[tuple[UUID, UUID]] = []

    async def get_membership(self, tenant_id: UUID, user_id: UUID) -> dict[str, str] | None:
        self.calls.append((tenant_id, user_id))
        return self.membership


def build_caller(role: UserRole) -> AuthTokenPayload:
    """Build one authenticated caller payload with a given platform role."""
    now = datetime.now(UTC)
    return AuthTokenPayload(
        user_id=CALLER_ID,
        role=role,
        token_id=UUID("dddddddd-0000-0000-0000-000000000001"),
        expires_at=now + timedelta(minutes=5),
        issued_at=now,
    )


# ---------------------------------------------------------------------------
# ensure_tenant_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_tenant_read_when_caller_is_platform_operator_skips_membership_lookup() -> (
    None
):
    """REQ: platform operator+ must bypass tenant membership entirely."""
    persistence = FakeTenantMembershipPersistence(membership=None)
    service = TenantAccessService(persistence)

    await service.ensure_tenant_read(TENANT_ID, build_caller("operator"))

    assert persistence.calls == []


@pytest.mark.asyncio
async def test_ensure_tenant_read_when_caller_is_active_member_passes() -> None:
    """REQ: an active tenant member may read tenant-scoped data."""
    persistence = FakeTenantMembershipPersistence(membership={"status": "active"})
    service = TenantAccessService(persistence)

    await service.ensure_tenant_read(TENANT_ID, build_caller("developer"))


@pytest.mark.asyncio
async def test_ensure_tenant_read_when_membership_missing_raises_denied() -> None:
    """REQ: a caller with no membership record must be denied read access."""
    persistence = FakeTenantMembershipPersistence(membership=None)
    service = TenantAccessService(persistence)

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.ensure_tenant_read(TENANT_ID, build_caller("developer"))
    assert excinfo.value.required_role == "member"


@pytest.mark.asyncio
async def test_ensure_tenant_read_when_membership_inactive_raises_denied() -> None:
    """REQ: an inactive membership must not grant read access."""
    persistence = FakeTenantMembershipPersistence(membership={"status": "suspended"})
    service = TenantAccessService(persistence)

    with pytest.raises(TenantAccessDeniedError):
        await service.ensure_tenant_read(TENANT_ID, build_caller("developer"))


# ---------------------------------------------------------------------------
# ensure_tenant_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_tenant_admin_when_caller_is_platform_admin_skips_membership_lookup() -> None:
    """REQ: platform admin+ must bypass tenant membership entirely."""
    persistence = FakeTenantMembershipPersistence(membership=None)
    service = TenantAccessService(persistence)

    await service.ensure_tenant_admin(TENANT_ID, build_caller("admin"))

    assert persistence.calls == []


@pytest.mark.asyncio
async def test_ensure_tenant_admin_when_membership_missing_raises_with_membership_reason() -> None:
    """REQ: no membership record must be distinguishable from an inactive one."""
    persistence = FakeTenantMembershipPersistence(membership=None)
    service = TenantAccessService(persistence)

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.ensure_tenant_admin(TENANT_ID, build_caller("developer"))
    assert excinfo.value.required_role == "tenant_membership"


@pytest.mark.asyncio
async def test_ensure_tenant_admin_when_membership_inactive_raises_with_active_reason() -> None:
    """REQ: an inactive membership must be distinguishable from a missing one."""
    persistence = FakeTenantMembershipPersistence(
        membership={"status": "suspended", "tenant_role": "admin"}
    )
    service = TenantAccessService(persistence)

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.ensure_tenant_admin(TENANT_ID, build_caller("developer"))
    assert excinfo.value.required_role == "active_membership"


@pytest.mark.asyncio
async def test_ensure_tenant_admin_when_tenant_role_insufficient_raises_with_admin_reason() -> None:
    """REQ: an active non-admin tenant role must be denied admin access."""
    persistence = FakeTenantMembershipPersistence(
        membership={"status": "active", "tenant_role": "developer"}
    )
    service = TenantAccessService(persistence)

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.ensure_tenant_admin(TENANT_ID, build_caller("developer"))
    assert excinfo.value.required_role == "admin"


@pytest.mark.asyncio
async def test_ensure_tenant_admin_when_tenant_role_is_admin_passes() -> None:
    """REQ: an active admin/owner tenant role must be granted admin access."""
    persistence = FakeTenantMembershipPersistence(
        membership={"status": "active", "tenant_role": "owner"}
    )
    service = TenantAccessService(persistence)

    await service.ensure_tenant_admin(TENANT_ID, build_caller("developer"))


# ---------------------------------------------------------------------------
# ensure_self_or_admin
# ---------------------------------------------------------------------------


def test_ensure_self_or_admin_when_caller_owns_the_resource_passes() -> None:
    """REQ: a caller acting on their own user record must be allowed, no lookup needed."""
    service = TenantAccessService(FakeTenantMembershipPersistence(membership=None))

    service.ensure_self_or_admin(CALLER_ID, build_caller("developer"))


def test_ensure_self_or_admin_when_caller_is_platform_admin_passes() -> None:
    """REQ: a platform admin may act on another user's record."""
    service = TenantAccessService(FakeTenantMembershipPersistence(membership=None))

    service.ensure_self_or_admin(OTHER_USER_ID, build_caller("admin"))


def test_ensure_self_or_admin_when_caller_is_neither_owner_nor_admin_raises_denied() -> None:
    """REQ: a non-admin caller must not act on another user's record."""
    service = TenantAccessService(FakeTenantMembershipPersistence(membership=None))

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        service.ensure_self_or_admin(OTHER_USER_ID, build_caller("developer"))
    assert excinfo.value.required_role == "self_or_admin"
