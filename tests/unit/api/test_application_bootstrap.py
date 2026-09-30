"""Specification tests for the process factory, lifecycle, and health boundary.

Architecture:
    test factory -> main.create_app / lifespan -> adapter-boundary fakes
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app import bootstrap, main
from app.adapters.cache import RedisConnectionManager
from app.adapters.postgresql import PostgresSessionProvider
from app.adapters.provider_transport import ProviderCircuitBreakerRegistry
from app.core.settings.models.circuit_breaker_config import (
    CircuitBreakerPolicyConfig,
    ProviderCircuitBreakerConfig,
)
from app.core.settings.models.global_config import GlobalConfig
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter

if TYPE_CHECKING:
    from contextlib import AsyncExitStack

    from app.core.settings.settings import ApplicationSettings


class MismatchedCircuitBreakerConfigLoader:
    """Return one unknown breaker override and record later startup work."""

    def __init__(self) -> None:
        """Start before optional cloud configuration has been loaded."""
        self.cloud_configs_loaded = False

    def load_global_config(self) -> GlobalConfig:
        """Return a global config containing an unknown provider override."""
        return GlobalConfig(
            provider_circuit_breakers=ProviderCircuitBreakerConfig(
                providers={"missing-provider": CircuitBreakerPolicyConfig()}
            )
        )

    def load_all_provider_configs(self) -> dict[str, object]:
        """Return the only provider known to the startup catalog."""
        return {"openai": object()}

    def load_all_cloud_configs(self) -> dict[object, object]:
        """Record work that must not happen after validation fails."""
        self.cloud_configs_loaded = True
        return {}


class DiagnosticPostgresProvider(PostgresSessionProvider):
    """Expose deterministic counters without opening a database pool."""

    def __init__(self) -> None:
        pass

    def stats(self) -> dict[str, int]:
        return {"health.ok": 4, "health.degraded": 0}


class DiagnosticRedisConnection(RedisConnectionManager):
    """Expose deterministic counters without opening a Redis connection."""

    def __init__(self) -> None:
        pass

    def stats(self) -> dict[str, int]:
        return {"connected": 1, "degraded": 0, "get.hit": 7}


@pytest.mark.parametrize("environment", ["development", "production"])
def test_factory_exposes_docs_only_outside_production(
    test_settings: ApplicationSettings, environment: str
) -> None:
    """REQ: public OpenAPI and interactive docs are absent in production."""
    # Arrange
    settings = test_settings.model_copy(update={"app_environment": environment})

    # Act
    app = main.create_app(settings)
    paths = {getattr(route, "path", None) for route in app.routes}

    # Assert
    assert ({"/docs", "/redoc", "/openapi.json"} <= paths) is (environment != "production")
    assert app.version == settings.service_version


def test_provider_config_rejects_unknown_breaker_override_before_later_startup_work(
    test_settings: ApplicationSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown provider policy keys fail before cloud config or pools are built."""
    # Arrange
    loader = MismatchedCircuitBreakerConfigLoader()
    monkeypatch.setattr(bootstrap, "ConfigLoader", lambda **_kwargs: loader)

    # Act / Assert
    with pytest.raises(ValueError, match="missing-provider"):
        bootstrap._load_provider_config(test_settings)
    assert loader.cloud_configs_loaded is False


@pytest.mark.asyncio
async def test_lifespan_passes_configured_service_name_to_logging(
    test_settings: ApplicationSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured logs use the service identity from validated settings."""
    # Arrange
    configured_logging: dict[str, object] = {}
    settings = test_settings.model_copy(update={"service_name": "routing-api"})

    def record_logging_configuration(**kwargs: object) -> None:
        configured_logging.update(kwargs)

    async def configure(
        app: FastAPI,
        settings: ApplicationSettings,
        stack: AsyncExitStack,
    ) -> None:
        return None

    monkeypatch.setattr(main, "configure_logging", record_logging_configuration)
    monkeypatch.setattr(main, "configure_runtime", configure)
    app = FastAPI()
    app.state.settings = settings

    # Act
    async with main.lifespan(app):
        pass

    # Assert
    assert configured_logging["service_name"] == "routing-api"


@pytest.mark.asyncio
async def test_lifespan_closes_all_resources_after_startup_failure(
    test_settings: ApplicationSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ: partially constructed pools close even when the next adapter fails."""
    # Arrange
    closed: list[str] = []

    async def configure(app: FastAPI, settings: ApplicationSettings, stack: AsyncExitStack) -> None:
        async def close(name: str) -> None:
            closed.append(name)

        stack.push_async_callback(close, "database")
        stack.push_async_callback(close, "redis")
        raise RuntimeError("next adapter failed")

    monkeypatch.setattr(main, "configure_runtime", configure)
    app = FastAPI()
    app.state.settings = test_settings

    # Act
    with pytest.raises(RuntimeError, match="next adapter failed"):
        async with main.lifespan(app):
            pass

    # Assert
    assert closed == ["redis", "database"]


@pytest.mark.asyncio
async def test_lifespan_continues_closing_after_closer_failure(
    test_settings: ApplicationSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ: one failing closer cannot prevent earlier pools from closing."""
    # Arrange
    closed: list[str] = []

    async def configure(app: FastAPI, settings: ApplicationSettings, stack: AsyncExitStack) -> None:
        async def close_database() -> None:
            closed.append("database")

        async def close_redis() -> None:
            closed.append("redis")
            raise RuntimeError("redis close failed")

        stack.push_async_callback(close_database)
        stack.push_async_callback(close_redis)

    monkeypatch.setattr(main, "configure_runtime", configure)
    app = FastAPI()
    app.state.settings = test_settings

    # Act
    with pytest.raises(RuntimeError, match="redis close failed"):
        async with main.lifespan(app):
            pass

    # Assert
    assert closed == ["redis", "database"]


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["development", "production"])
async def test_readiness_hides_dependency_details_in_production(
    test_settings: ApplicationSettings, environment: str
) -> None:
    """REQ: a public failed probe returns 503 without operational data in production."""
    # Arrange
    app = main.create_app(test_settings.model_copy(update={"app_environment": environment}))
    app.state.postgres_session_provider = type(
        "DatabaseProbe", (), {"health_check": AsyncMock(return_value=True)}
    )()
    app.state.redis_connection = type(
        "CacheProbe", (), {"health_check": AsyncMock(return_value=False)}
    )()

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    # Assert
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert ("dependencies" in response.json()) is (environment != "production")
    assert "cache_stats" not in response.json()
    assert "database_stats" not in response.json()


@pytest.mark.asyncio
async def test_readiness_returns_ready_when_both_dependencies_pass(
    test_settings: ApplicationSettings,
) -> None:
    """REQ: healthy dependencies allow traffic and report only status in production."""
    # Arrange
    app = main.create_app(test_settings.model_copy(update={"app_environment": "production"}))
    app.state.postgres_session_provider = type(
        "DatabaseProbe", (), {"health_check": AsyncMock(return_value=True)}
    )()
    app.state.redis_connection = type(
        "CacheProbe", (), {"health_check": AsyncMock(return_value=True)}
    )()

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_dependency_probe_raises(
    test_settings: ApplicationSettings,
) -> None:
    """REQ: a failing adapter probe cannot accidentally become a 500 response."""
    # Arrange
    app = main.create_app(test_settings.model_copy(update={"app_environment": "production"}))
    app.state.postgres_session_provider = type(
        "DatabaseProbe", (), {"health_check": AsyncMock(side_effect=OSError("dial failed"))}
    )()
    app.state.redis_connection = type(
        "CacheProbe", (), {"health_check": AsyncMock(return_value=True)}
    )()

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    # Assert
    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


@pytest.mark.asyncio
async def test_diagnostics_exposes_process_local_operational_snapshots(
    test_settings: ApplicationSettings,
) -> None:
    """Operators can explain stream saturation without exposing provider names."""
    # Arrange
    app = main.create_app(test_settings)
    limiter = WorkerStreamCapacityLimiter(max_concurrent=3, retry_after_seconds=1)
    lease = await limiter.acquire()
    breakers = ProviderCircuitBreakerRegistry(ProviderCircuitBreakerConfig())
    await breakers.get_breaker("private-provider-name")
    app.state.stream_capacity_limiter = limiter
    app.state.provider_circuit_breaker_registry = breakers
    app.state.postgres_session_provider = DiagnosticPostgresProvider()
    app.state.redis_connection = DiagnosticRedisConnection()

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/health/diagnostics")
        second = await client.get("/health/diagnostics")
    await lease.release()

    # Assert
    expected = {
        "scope": "process-local",
        "streaming": {
            "available": True,
            "active_streams": 1,
            "max_concurrent_streams": 3,
        },
        "provider_circuits": {
            "available": True,
            "states": {"closed": 1, "open": 0, "half_open": 0},
        },
        "postgresql": {
            "available": True,
            "counters": {"health.ok": 4, "health.degraded": 0},
        },
        "redis": {
            "available": True,
            "counters": {"connected": 1, "degraded": 0, "get.hit": 7},
        },
    }
    assert first.status_code == 200
    assert first.json() == expected
    assert second.json() == expected
    assert "private-provider-name" not in first.text


@pytest.mark.asyncio
async def test_diagnostics_handles_runtime_components_not_yet_initialized(
    test_settings: ApplicationSettings,
) -> None:
    """An early diagnostic request remains useful instead of becoming a 500."""
    app = main.create_app(test_settings)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "process-local"
    assert payload["streaming"] == {"available": False}
    assert payload["provider_circuits"]["available"] is False
    assert payload["postgresql"] == {"available": False, "counters": {}}
    assert payload["redis"] == {"available": False, "counters": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("settings_value", [None, "wrong-type"])
async def test_readiness_returns_503_when_settings_state_is_unusable(
    test_settings: ApplicationSettings,
    settings_value: str | None,
) -> None:
    """REQ: incomplete startup state is unready and never leaks details or returns 500."""
    # Arrange
    app = main.create_app(test_settings)
    if settings_value is None:
        del app.state.settings
    else:
        app.state.settings = settings_value
    app.state.postgres_session_provider = type(
        "DatabaseProbe", (), {"health_check": AsyncMock(return_value=True)}
    )()
    app.state.redis_connection = type(
        "CacheProbe", (), {"health_check": AsyncMock(return_value=True)}
    )()

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    # Assert
    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}
