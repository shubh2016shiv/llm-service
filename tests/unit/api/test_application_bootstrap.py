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

from app import main

if TYPE_CHECKING:
    from contextlib import AsyncExitStack

    from app.core.settings.settings import ApplicationSettings


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
