"""Reconciliation only accepts one tenant's minted credential versions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import SecretStr

from smoke_test import credential_reconciliation
from smoke_test.credential_reconciliation import validate_candidate

TENANT = UUID("10000000-0000-0000-0000-000000000001")
VERSION = "20000000-0000-0000-0000-000000000002"


@pytest.mark.parametrize(
    "reference",
    [
        f"tenant-deployments/{TENANT}/prod/versions/{VERSION}",
        f"user-entitlements/{TENANT}/user/name-hash/versions/{VERSION}",
    ],
)
def test_reconcile_accepts_one_tenant_versioned_credential(reference: str) -> None:
    """REQ: only minted credential paths may reach the deletion boundary."""
    assert validate_candidate(reference, TENANT) == reference


@pytest.mark.parametrize(
    "reference",
    [
        f"tenant-deployments/{UUID(int=3)}/prod/versions/{VERSION}",
        f"tenant-deployments/{TENANT}/prod/{VERSION}",
        f"tenant-deployments/{TENANT}/prod/versions/not-a-uuid",
        f"tenant-deployments/{TENANT}/../prod/versions/{VERSION}",
        f"user-entitlements/{TENANT}/user/name-hash/versions/{VERSION}/extra",
    ],
)
def test_reconcile_rejects_unsafe_or_non_versioned_path(reference: str) -> None:
    """REQ: a cleanup command cannot delete unrelated Vault metadata."""
    with pytest.raises(ValueError):
        validate_candidate(reference, TENANT)


@pytest.mark.asyncio
async def test_reconcile_refuses_to_delete_a_database_referenced_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ: a live DB reference always prevents Vault metadata deletion."""
    connection = SimpleNamespace(scalar=AsyncMock(return_value=True))

    @asynccontextmanager
    async def connection_context():
        yield connection

    engine = SimpleNamespace(connect=connection_context, dispose=AsyncMock())
    monkeypatch.setattr(credential_reconciliation, "create_async_engine", lambda _: engine)
    monkeypatch.setattr(
        credential_reconciliation,
        "get_application_settings",
        lambda: SimpleNamespace(database_url=SecretStr("unused")),
    )

    with pytest.raises(RuntimeError, match="still referenced"):
        await credential_reconciliation.reconcile(
            f"tenant-deployments/{TENANT}/prod/versions/{VERSION}", TENANT, apply=True
        )

    assert connection.scalar.await_count == 1
    engine.dispose.assert_awaited_once()
