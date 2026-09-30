"""Tenant and owner scope must be enforced by mutation SQL itself."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.database.tenant_deployments import TenantDeploymentPersistence
from app.database.tenant_memberships import TenantMembershipPersistence
from app.database.user_entitlements import UserEntitlementPersistence


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", ["deployment", "entitlement", "membership"])
async def test_delete_by_id_binds_authorized_scope(entity: str) -> None:
    """REQ: an ID alone cannot authorize deletion in another tenant."""
    record_id, tenant_id, user_id = uuid4(), uuid4(), uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)))

    @asynccontextmanager
    async def session_context():
        yield session

    if entity == "deployment":
        repository = object.__new__(TenantDeploymentPersistence)
        repository.get_session = session_context
        deleted = await repository.delete_deployment(record_id, tenant_id)
    elif entity == "entitlement":
        repository = object.__new__(UserEntitlementPersistence)
        repository.get_session = session_context
        deleted = await repository.delete_entitlement(record_id, tenant_id, user_id)
    else:
        repository = object.__new__(TenantMembershipPersistence)
        repository.get_session = session_context
        deleted = await repository.delete_membership_by_id(record_id, tenant_id)

    statement, params = session.execute.await_args.args
    assert deleted is False
    assert "tenant_id = :tenant_id" in str(statement)
    assert params["tenant_id"] == str(tenant_id)
    if entity == "entitlement":
        assert "user_id = :user_id" in str(statement)
        assert params["user_id"] == str(user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", ["deployment", "entitlement", "membership"])
async def test_update_by_id_binds_authorized_scope(entity: str) -> None:
    """REQ: an ID alone cannot authorize mutation in another tenant."""
    record_id, tenant_id, user_id = uuid4(), uuid4(), uuid4()
    result = SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: None))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    @asynccontextmanager
    async def session_context():
        yield session

    if entity == "deployment":
        repository = object.__new__(TenantDeploymentPersistence)
        repository.get_session = session_context
        updated = await repository.update_deployment(record_id, tenant_id, status="active")
    elif entity == "entitlement":
        repository = object.__new__(UserEntitlementPersistence)
        repository.get_session = session_context
        updated = await repository.update_entitlement(
            record_id, tenant_id, user_id, status="active"
        )
    else:
        repository = object.__new__(TenantMembershipPersistence)
        repository.get_session = session_context
        updated = await repository.update_membership(record_id, tenant_id, status="active")

    statement, params = session.execute.await_args.args
    assert updated is None
    assert "tenant_id = :tenant_id" in str(statement)
    assert params["tenant_id"] == str(tenant_id)
    if entity == "entitlement":
        assert "user_id = :user_id" in str(statement)
        assert params["user_id"] == str(user_id)
