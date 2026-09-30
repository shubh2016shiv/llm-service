"""Contract and catalog-validation tests for resolved inference routes."""

from __future__ import annotations

import pytest

from app.core.settings.models.model_config import ModelCapability
from app.inference_routing.exceptions import OperationNotSupportedError, RoutingCatalogDriftError
from app.inference_routing.models import ResolvedRoute
from app.schemas.enums import OperationType
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_KEY,
    ENTITLEMENT_ID,
    MODEL_NAME,
    PROVIDER_NAME,
    TENANT_ID,
    FakeConfigLoader,
    build_model_spec,
    build_provider_static_config,
    build_tenant_config,
    build_user_entitlement_config,
)
from tests.unit.inference_routing.routing_fakes import (
    FakeInferenceRoutingConfigReader,
    build_resolution_request,
    build_route_resolver,
)

EXPECTED_ROUTE_FIELDS = {
    "tenant_id",
    "deployment_key",
    "provider_static_config",
    "provider_name",
    "model_name",
    "api_endpoint_url",
    "cloud_region",
    "secret_reference",
    "effective_timeout_seconds",
    "effective_temperature",
    "effective_max_tokens",
    "extra_headers",
    "extra_config",
    "quota_key",
    "route_fingerprint",
}


def build_entitlement_reader(
    *,
    model_name: str = MODEL_NAME,
) -> FakeInferenceRoutingConfigReader:
    """Build a reader containing the exact authorized entitlement."""
    return FakeInferenceRoutingConfigReader(
        tenant=build_tenant_config(),
        entitlement=build_user_entitlement_config(model_name=model_name),
    )


@pytest.mark.asyncio
async def test_resolved_route_contains_only_execution_fields() -> None:
    """The output omits authorization and persistence implementation details."""
    route = await build_route_resolver(build_entitlement_reader()).resolve_route(
        build_resolution_request()
    )

    assert set(ResolvedRoute.model_fields) == EXPECTED_ROUTE_FIELDS
    assert route.tenant_id == TENANT_ID
    assert route.deployment_key == DEPLOYMENT_KEY
    assert route.quota_key == str(ENTITLEMENT_ID)


@pytest.mark.asyncio
async def test_entitlement_route_uses_catalog_defaults() -> None:
    """Catalog defaults fill operational values absent from entitlements."""
    provider = build_provider_static_config(
        default_timeout_seconds=45.0,
        default_temperature=0.3,
    )

    route = await build_route_resolver(
        build_entitlement_reader(),
        FakeConfigLoader({PROVIDER_NAME: provider}),
    ).resolve_route(build_resolution_request())

    assert route.effective_timeout_seconds == 45.0
    assert route.effective_temperature == 0.3
    assert route.effective_max_tokens == 4096
    assert route.extra_config == {"owner": "user"}


@pytest.mark.asyncio
async def test_identical_routes_have_identical_fingerprints() -> None:
    """Fingerprint generation is deterministic for provider caching."""
    resolver = build_route_resolver(build_entitlement_reader())

    first = await resolver.resolve_route(build_resolution_request())
    second = await resolver.resolve_route(build_resolution_request())

    assert first.route_fingerprint == second.route_fingerprint
    assert len(first.route_fingerprint) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_provider",
    [
        build_provider_static_config(extra_default_headers={"X-API-Version": "2026-09-29"}),
        build_provider_static_config(default_timeout_seconds=45.0),
        build_provider_static_config(
            model_spec=build_model_spec().model_copy(update={"max_output_tokens": 2048})
        ),
    ],
)
async def test_catalog_changes_produce_a_new_route_fingerprint(changed_provider) -> None:
    """Cached providers cannot survive changes to route-carrying catalog values."""
    reader = build_entitlement_reader()
    original = await build_route_resolver(reader).resolve_route(build_resolution_request())
    changed = await build_route_resolver(
        reader,
        FakeConfigLoader({PROVIDER_NAME: changed_provider}),
    ).resolve_route(build_resolution_request())

    assert changed.route_fingerprint != original.route_fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("loader", "model_name", "operation", "expected_error"),
    [
        (FakeConfigLoader(), MODEL_NAME, OperationType.CHAT, RoutingCatalogDriftError),
        (
            FakeConfigLoader({PROVIDER_NAME: build_provider_static_config()}),
            "missing-model",
            OperationType.CHAT,
            RoutingCatalogDriftError,
        ),
        (
            FakeConfigLoader(
                {
                    PROVIDER_NAME: build_provider_static_config(
                        model_spec=build_model_spec(capabilities=frozenset({ModelCapability.EMBED}))
                    )
                }
            ),
            MODEL_NAME,
            OperationType.CHAT,
            OperationNotSupportedError,
        ),
    ],
)
async def test_invalid_catalog_route_is_rejected(
    loader,
    model_name,
    operation,
    expected_error,
) -> None:
    """Missing provider, model, and capability each fail explicitly."""
    with pytest.raises(expected_error):
        await build_route_resolver(
            build_entitlement_reader(model_name=model_name), loader
        ).resolve_route(build_resolution_request(operation))
