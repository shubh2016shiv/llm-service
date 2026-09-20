"""Behavioral tests for provider transport selection."""

from __future__ import annotations

from typing import cast

import httpx
import pytest

from app.adapters.provider_transport import ProviderTransportFactory
from app.core.settings.models.global_config import HTTPPoolConfig
from app.core.settings.models.provider_config import ProviderType


@pytest.mark.asyncio
async def test_create_transport_for_rest_provider_returns_async_http_client() -> None:
    """REST configuration selects the shared-pool HTTP transport path."""
    factory = ProviderTransportFactory(HTTPPoolConfig())

    transport = factory.create_transport(ProviderType.REST_API)

    assert isinstance(transport, httpx.AsyncClient)
    await factory.aclose()


@pytest.mark.asyncio
async def test_rest_providers_borrow_one_factory_owned_client() -> None:
    """All REST providers share one client so connection-pool ownership is unambiguous."""
    factory = ProviderTransportFactory(HTTPPoolConfig())

    first = factory.create_transport(ProviderType.REST_API)
    second = factory.create_transport(ProviderType.REST_API)

    assert second is first
    await factory.aclose()


@pytest.mark.asyncio
async def test_factory_rejects_use_after_close_and_close_is_idempotent() -> None:
    """Shutdown poisons the factory and repeated cleanup remains harmless."""
    factory = ProviderTransportFactory(HTTPPoolConfig())

    await factory.aclose()
    await factory.aclose()

    with pytest.raises(RuntimeError, match="is closed"):
        factory.create_transport(ProviderType.REST_API)


@pytest.mark.asyncio
async def test_rest_client_uses_configured_pool_timeout() -> None:
    """Pool saturation has an explicit configurable upper wait bound."""
    factory = ProviderTransportFactory(HTTPPoolConfig(pool_timeout_seconds=1.25))

    transport = factory.create_transport(ProviderType.REST_API)

    assert isinstance(transport, httpx.AsyncClient)
    assert transport.timeout.pool == 1.25
    await factory.aclose()


def test_http_pool_rejects_more_idle_connections_than_total_connections() -> None:
    """Contradictory pool limits fail during startup configuration validation."""
    with pytest.raises(ValueError, match="cannot exceed max_connections"):
        HTTPPoolConfig(max_connections=10, max_keepalive_connections=11)


@pytest.mark.asyncio
async def test_create_transport_for_unknown_type_reports_supported_values() -> None:
    """Invalid configuration fails during provider construction with useful context."""
    factory = ProviderTransportFactory(HTTPPoolConfig())

    with pytest.raises(ValueError, match="Expected one of: rest_api, aws_sdk, grpc"):
        factory.create_transport(cast("ProviderType", "websocket"))

    await factory.aclose()


@pytest.mark.asyncio
async def test_create_transport_for_unimplemented_grpc_fails_immediately() -> None:
    """Reserved gRPC configuration cannot create a partially usable provider."""
    factory = ProviderTransportFactory(HTTPPoolConfig())

    with pytest.raises(NotImplementedError, match="not yet implemented"):
        factory.create_transport(ProviderType.GRPC)

    await factory.aclose()
