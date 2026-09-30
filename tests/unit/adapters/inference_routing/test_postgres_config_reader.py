"""Contract tests for the PostgreSQL-backed routing reader."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.inference_routing import PostgresInferenceRoutingConfigReader
from tests.unit.adapters.inference_routing.routing_row_fakes import (
    entitlement_row,
    tenant_row,
)
from tests.unit.inference_routing.conftest import (
    ENTITLEMENT_ID,
    PROVIDER_NAME,
    TENANT_ID,
)
from tests.unit.inference_routing.routing_fakes import build_resolution_request


class FakeRoutingPersistence:
    """Provide controlled projections and record the entitlement identity."""

    def __init__(self) -> None:
        self.tenant_row: dict[str, Any] | None = None
        self.entitlement_row: dict[str, Any] | None = None
        self.entitlement_id: object | None = None

    async def get_tenant_config_for_routing(self, tenant_id):
        """Return the configured tenant projection."""
        return self.tenant_row

    async def get_routing_entitlement_for_route(self, **arguments):
        """Return one projection and record the exact requested grant."""
        self.entitlement_id = arguments["entitlement_id"]
        return self.entitlement_row


def build_reader(persistence: FakeRoutingPersistence) -> PostgresInferenceRoutingConfigReader:
    """Build the reader with one fake satisfying both persistence protocols."""
    return PostgresInferenceRoutingConfigReader(persistence, persistence)


@pytest.mark.asyncio
async def test_read_tenant_config_maps_database_projection() -> None:
    """Untrusted tenant rows cross the validated model boundary."""
    persistence = FakeRoutingPersistence()
    persistence.tenant_row = tenant_row()

    tenant = await build_reader(persistence).read_tenant_config(TENANT_ID)

    assert tenant is not None
    assert tenant.is_active
    assert tenant.allowed_provider_names == frozenset({PROVIDER_NAME})


@pytest.mark.asyncio
async def test_read_tenant_config_null_allow_list_permits_every_provider() -> None:
    """Database NULL means no restriction: every provider is permitted."""
    persistence = FakeRoutingPersistence()
    row = tenant_row()
    row["allowed_provider_names"] = None
    persistence.tenant_row = row

    tenant = await build_reader(persistence).read_tenant_config(TENANT_ID)

    assert tenant is not None
    assert tenant.allowed_provider_names is None
    assert tenant.allows_provider(PROVIDER_NAME)
    assert tenant.allows_provider("some-other-provider")


@pytest.mark.asyncio
async def test_read_tenant_config_empty_allow_list_permits_no_provider() -> None:
    """Database empty list means an empty permitted set, not 'no restriction'."""
    persistence = FakeRoutingPersistence()
    row = tenant_row()
    row["allowed_provider_names"] = []
    persistence.tenant_row = row

    tenant = await build_reader(persistence).read_tenant_config(TENANT_ID)

    assert tenant is not None
    assert tenant.allowed_provider_names == frozenset()
    assert not tenant.allows_provider(PROVIDER_NAME)


@pytest.mark.asyncio
async def test_read_entitlement_config_uses_exact_authorized_identity() -> None:
    """The adapter requests and maps only the authorization-approved grant."""
    persistence = FakeRoutingPersistence()
    persistence.entitlement_row = entitlement_row()

    entitlement = await build_reader(persistence).read_entitlement_config(
        build_resolution_request()
    )

    assert entitlement is not None
    assert entitlement.entitlement_id == ENTITLEMENT_ID
    assert persistence.entitlement_id == ENTITLEMENT_ID


@pytest.mark.asyncio
async def test_read_entitlement_config_with_missing_row_returns_none() -> None:
    """Revocation/deletion remains distinguishable from a malformed row."""
    persistence = FakeRoutingPersistence()

    entitlement = await build_reader(persistence).read_entitlement_config(
        build_resolution_request()
    )

    assert entitlement is None
