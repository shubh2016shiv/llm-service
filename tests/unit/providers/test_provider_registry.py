"""Behavior tests for bounded, credential-aware provider instance caching."""

from __future__ import annotations

import asyncio

import pytest
from aiobreaker import CircuitBreaker
from pydantic import ValidationError

from app.core.exceptions import ConfigurationError
from app.core.settings.models.provider_config import (
    AuthMode,
    ProviderAuthConfig,
    ProviderImplementation,
    ProviderStaticConfig,
    ProviderType,
)
from app.inference_routing.models import ResolvedRoute
from app.providers.registry import ProviderRegistry
from tests.unit.inference_routing.conftest import (
    API_ENDPOINT,
    DEPLOYMENT_KEY,
    MODEL_NAME,
    PROVIDER_NAME,
    TENANT_ID,
    build_provider_static_config,
)


class FakeTransportFactory:
    """Return an inert transport because registry tests never make HTTP calls."""

    def create_transport(self, provider_type):
        """Return a unique inert transport object."""
        return object()


class FakeCircuitBreakerRegistry:
    """Return one real breaker satisfying provider construction."""

    async def get_breaker(self, provider_name: str) -> CircuitBreaker:
        """Return a fresh breaker for the requested provider."""
        return CircuitBreaker()


class RecordingSecretStore:
    """Count secret reads and optionally hold them for concurrency tests."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.read_count = 0
        self.gate = gate

    async def get_secret(self, reference: str, *, tenant_id: str) -> str:
        """Return a rotating value after an optional synchronization gate."""
        self.read_count += 1
        if self.gate is not None:
            await self.gate.wait()
        return f"secret-version-{self.read_count}"


def build_route(fingerprint: str, *, implementation_class: str | None = None) -> ResolvedRoute:
    """Build the smallest valid execution route for registry behavior tests."""
    static = build_provider_static_config()
    if implementation_class is not None:
        static = static.model_copy(update={"implementation_class": implementation_class})
    return ResolvedRoute(
        tenant_id=TENANT_ID,
        deployment_key=DEPLOYMENT_KEY,
        provider_static_config=static,
        provider_name=PROVIDER_NAME,
        model_name=MODEL_NAME,
        api_endpoint_url=API_ENDPOINT,
        secret_reference="secret/acme/openai",
        effective_timeout_seconds=30,
        effective_temperature=0.2,
        effective_max_tokens=100,
        quota_key="grant-1",
        route_fingerprint=fingerprint,
    )


def build_registry(
    secret_store: RecordingSecretStore,
    clock,
    *,
    max_entries: int = 2,
) -> ProviderRegistry:
    """Build a registry with deterministic time and narrow fakes."""
    return ProviderRegistry(
        transport_factory=FakeTransportFactory(),
        circuit_breaker_registry=FakeCircuitBreakerRegistry(),
        secret_store=secret_store,
        cache_ttl_seconds=60,
        max_cached_providers=max_entries,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_get_provider_with_fresh_entry_reuses_instance_and_secret() -> None:
    """A hot route avoids repeated provider construction and secret reads."""
    secret_store = RecordingSecretStore()
    registry = build_registry(secret_store, lambda: 0.0)

    first = await registry.get_provider(build_route("a" * 64))
    second = await registry.get_provider(build_route("a" * 64))

    assert first is second
    assert secret_store.read_count == 1


@pytest.mark.asyncio
async def test_get_provider_after_ttl_reloads_rotated_secret() -> None:
    """TTL expiry prevents a credential reference from staying stale forever."""
    now = [0.0]
    secret_store = RecordingSecretStore()
    registry = build_registry(secret_store, lambda: now[0])
    first = await registry.get_provider(build_route("a" * 64))

    now[0] = 61.0
    second = await registry.get_provider(build_route("a" * 64))

    assert first is not second
    assert secret_store.read_count == 2


@pytest.mark.asyncio
async def test_get_provider_with_concurrent_miss_builds_once() -> None:
    """Concurrent callers for one route share construction and secret lookup."""
    gate = asyncio.Event()
    secret_store = RecordingSecretStore(gate)
    registry = build_registry(secret_store, lambda: 0.0)
    route = build_route("a" * 64)
    first = asyncio.create_task(registry.get_provider(route))
    second = asyncio.create_task(registry.get_provider(route))
    await asyncio.sleep(0)

    gate.set()
    providers = await asyncio.gather(first, second)

    assert providers[0] is providers[1]
    assert secret_store.read_count == 1


@pytest.mark.asyncio
async def test_get_provider_over_capacity_evicts_least_recent_route() -> None:
    """High route cardinality remains bounded by deterministic LRU eviction."""
    secret_store = RecordingSecretStore()
    registry = build_registry(secret_store, lambda: 0.0, max_entries=1)
    await registry.get_provider(build_route("a" * 64))
    await registry.get_provider(build_route("b" * 64))

    await registry.get_provider(build_route("a" * 64))

    assert secret_store.read_count == 3


@pytest.mark.asyncio
async def test_get_provider_with_unregistered_class_fails_before_import() -> None:
    """YAML cannot turn arbitrary Python import paths into executable code."""
    registry = build_registry(RecordingSecretStore(), lambda: 0.0)
    route = build_route("a" * 64, implementation_class="malicious.module.Provider")

    with pytest.raises(ConfigurationError, match="not registered"):
        await registry.get_provider(route)


def test_provider_config_with_unknown_implementation_fails_validation() -> None:
    """An arbitrary import path is rejected when static YAML is parsed."""
    payload = build_provider_static_config().model_dump(mode="json")
    payload["implementation_class"] = "malicious.module.Provider"

    with pytest.raises(ValidationError):
        ProviderStaticConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_get_bedrock_provider_uses_iam_without_secret_lookup() -> None:
    """SigV4 providers rely on the AWS credential chain, not API-key storage."""
    secret_store = RecordingSecretStore()
    registry = build_registry(secret_store, lambda: 0.0)
    route = build_route("a" * 64)
    static = route.provider_static_config.model_copy(
        update={
            "provider_name": "bedrock",
            "provider_type": ProviderType.AWS_SDK,
            "implementation_class": ProviderImplementation.BEDROCK,
            "auth": ProviderAuthConfig(mode=AuthMode.AWS_SIGV4),
        }
    )
    route = route.model_copy(
        update={"provider_name": "bedrock", "provider_static_config": static}
    )

    await registry.get_provider(route)

    assert secret_store.read_count == 0
