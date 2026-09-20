"""Behavior tests for fail-closed inference route resolution."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.exceptions import ConfigurationError, TenantNotFoundError, TenantSuspendedError
from app.inference_routing.exceptions import (
    AuthorizedEntitlementUnavailableError,
    ProviderNotAllowedError,
)
from tests.unit.inference_routing.conftest import (
    ENTITLEMENT_ID,
    build_tenant_config,
    build_user_entitlement_config,
)
from tests.unit.inference_routing.routing_fakes import (
    FakeInferenceRoutingConfigReader,
    build_resolution_request,
    build_route_resolver,
)


@pytest.mark.asyncio
async def test_resolve_route_with_authorized_entitlement_returns_user_route() -> None:
    """The exact authorization grant becomes the execution route."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(),
    )

    route = await build_route_resolver(reader).resolve_route(build_resolution_request())

    assert route.quota_key == str(ENTITLEMENT_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("entitlement", [None, build_user_entitlement_config(is_active=False)])
async def test_resolve_route_with_unavailable_grant_fails_closed(entitlement) -> None:
    """A revoked or deleted grant never falls back to another credential."""
    reader = FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=entitlement,
    )

    with pytest.raises(AuthorizedEntitlementUnavailableError):
        await build_route_resolver(reader).resolve_route(build_resolution_request())


@pytest.mark.asyncio
async def test_resolve_route_with_wrong_entitlement_from_reader_raises_configuration_error() -> None:
    """A broken adapter cannot silently substitute another authorization grant."""
    wrong = build_user_entitlement_config().model_copy(
        update={"entitlement_id": UUID("30000000-0000-0000-0000-000000000099")}
    )
    reader = FakeInferenceRoutingConfigReader(build_tenant_config(), wrong)

    with pytest.raises(ConfigurationError, match="other than the authorized"):
        await build_route_resolver(reader).resolve_route(build_resolution_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant", "expected_error"),
    [(None, TenantNotFoundError), (build_tenant_config(status="suspended"), TenantSuspendedError)],
)
async def test_resolve_route_with_ineligible_tenant_raises_tenant_error(
    tenant,
    expected_error,
) -> None:
    """Missing and suspended tenants fail before entitlement execution."""
    reader = FakeInferenceRoutingConfigReader(tenant=tenant)

    with pytest.raises(expected_error):
        await build_route_resolver(reader).resolve_route(build_resolution_request())


@pytest.mark.asyncio
async def test_resolve_route_with_disallowed_provider_raises_policy_error() -> None:
    """Current tenant policy is enforced against the approved entitlement."""
    tenant = build_tenant_config(allowed_provider_names=frozenset({"anthropic"}))
    reader = FakeInferenceRoutingConfigReader(tenant, build_user_entitlement_config())

    with pytest.raises(ProviderNotAllowedError):
        await build_route_resolver(reader).resolve_route(build_resolution_request())
