"""Contract tests for the PostgreSQL-backed routing reader."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.inference_routing import PostgresInferenceRoutingConfigReader
from tests.unit.inference_routing.conftest import (
    API_ENDPOINT,
    ENTITLEMENT_ID,
    MODEL_NAME,
    PROVIDER_NAME,
    TENANT_ID,
    USER_ID,
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


def tenant_row() -> dict[str, Any]:
    """Build a realistic tenant routing projection."""
    return {
        "tenant_id": TENANT_ID, "tenant_name": "Acme", "tenant_slug": "acme",
        "status": "active", "tier": "enterprise",
        "rate_limit_requests_per_minute": 100,
        "rate_limit_tokens_per_minute": 10_000,
        "rate_limit_concurrent_requests": 5,
        "allowed_provider_names": [PROVIDER_NAME],
    }


def entitlement_row() -> dict[str, Any]:
    """Build a realistic entitlement routing projection."""
    return {
        "entitlement_id": ENTITLEMENT_ID, "user_id": USER_ID, "tenant_id": TENANT_ID,
        "entitlement_name": "Personal route", "provider_name": PROVIDER_NAME,
        "model_name": MODEL_NAME, "api_endpoint_url": API_ENDPOINT,
        "secret_reference": "secret/user/openai-key", "cloud_provider": None,
        "cloud_region": None, "extra_config": {"owner": "user"}, "status": "active",
    }


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
