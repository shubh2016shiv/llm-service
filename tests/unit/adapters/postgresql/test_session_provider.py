"""Verify PostgreSQL transaction lifecycle behavior.

Architecture:
    Persistence operation -> PostgresSessionProvider -> commit or rollback
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, cast

import pytest

from app.adapters.postgresql import PostgresSessionProvider
from app.adapters.postgresql import session_provider as session_provider_module
from app.core.settings.models.infrastructure_config import DatabaseConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def build_unconfigured_provider() -> PostgresSessionProvider:
    """Construct the provider without opening a real database pool."""
    provider = object.__new__(PostgresSessionProvider)
    provider._engine = None
    provider._session_factory = None
    provider._stats = defaultdict(int)
    provider._health_degraded = False
    return provider


class FailingRollbackSession:
    """Fake session whose rollback fails after the application error."""

    async def __aenter__(self) -> FailingRollbackSession:
        """Enter the fake async session context."""
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Exit without suppressing exceptions."""

    async def commit(self) -> None:
        """Record a commit attempt; unused in the failure path."""

    async def rollback(self) -> None:
        """Simulate a connection failure while rolling back."""
        raise RuntimeError("rollback connection lost")


class FakeSessionFactory:
    """Callable factory returning one controlled fake session."""

    def __init__(self, session: FailingRollbackSession) -> None:
        """Store the session returned by each call."""
        self.session = session

    def __call__(self) -> FailingRollbackSession:
        """Return the controlled session context manager."""
        return self.session


class RecordingSession:
    """Fake session that records the commit/rollback lifecycle calls."""

    def __init__(self) -> None:
        """Start with no lifecycle calls recorded."""
        self.commit_called = False
        self.rollback_called = False

    async def __aenter__(self) -> RecordingSession:
        """Enter the fake async session context."""
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Exit without suppressing exceptions."""

    async def commit(self) -> None:
        """Record a commit call."""
        self.commit_called = True

    async def rollback(self) -> None:
        """Record a rollback call."""
        self.rollback_called = True


class RecordingSessionFactory:
    """Callable factory returning one recording fake session."""

    def __init__(self, session: RecordingSession) -> None:
        """Store the session returned by each call."""
        self.session = session

    def __call__(self) -> RecordingSession:
        """Return the controlled session context manager."""
        return self.session


class FakeEngineConnection:
    """Fake pooled connection that records the health-check query."""

    def __init__(self) -> None:
        """Start with no query recorded."""
        self.executed = False

    async def __aenter__(self) -> FakeEngineConnection:
        """Enter the fake connection context."""
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Exit without suppressing exceptions."""

    async def execute(self, _statement: object) -> None:
        """Record that the probe query ran."""
        self.executed = True


class HealthyFakeEngine:
    """Fake engine whose pooled connections always answer, and records disposal."""

    def __init__(self) -> None:
        """Prepare one answering connection."""
        self.connection = FakeEngineConnection()
        self.disposed = False

    def connect(self) -> FakeEngineConnection:
        """Return the answering connection context manager."""
        return self.connection

    async def dispose(self) -> None:
        """Record pool disposal."""
        self.disposed = True


class BrokenFakeEngine:
    """Fake engine that refuses every connection attempt."""

    def connect(self) -> FakeEngineConnection:
        """Raise as if the database server were unreachable."""
        raise RuntimeError("connection refused")


class RecordingEngineBuilder:
    """Stand in for SQLAlchemy's engine factory and capture its options."""

    def __init__(self) -> None:
        """Start without a recorded construction call."""
        self.url = ""
        self.options: dict[str, object] = {}

    def __call__(self, url: str, **options: object) -> AsyncEngine:
        """Record the URL/options and return a harmless fake engine."""
        self.url = url
        self.options = options
        return cast("AsyncEngine", HealthyFakeEngine())


class FailingDisposeEngine(HealthyFakeEngine):
    """Fake engine whose pool cleanup fails during shutdown."""

    async def dispose(self) -> None:
        """Raise after shutdown has begun."""
        raise RuntimeError("dispose failed")


def test_constructor_configures_bounded_connect_and_command_timeouts(monkeypatch) -> None:
    """Pool creation must bound both connection setup and command response time."""
    engine_builder = RecordingEngineBuilder()
    monkeypatch.setattr(session_provider_module, "create_async_engine", engine_builder)
    config = DatabaseConfig(
        database_url="postgresql+asyncpg://user:secret@database/example",
        database_connect_timeout_seconds=4.0,
        database_statement_timeout_ms=12_000,
    )

    PostgresSessionProvider(config)

    connect_args = cast("dict[str, object]", engine_builder.options["connect_args"])
    assert connect_args["timeout"] == 4.0
    assert connect_args["command_timeout"] == 12.0
    assert "secret" not in str(engine_builder.options)


@pytest.mark.asyncio
async def test_get_session_when_rollback_fails_preserves_application_exception() -> None:
    """Ensure rollback failure does not hide the original application exception."""
    session_factory = FakeSessionFactory(FailingRollbackSession())
    provider = build_unconfigured_provider()
    provider._session_factory = cast(
        "async_sessionmaker[AsyncSession]",
        session_factory,
    )

    with pytest.raises(ValueError, match="application operation failed"):
        async with provider.get_session():
            raise ValueError("application operation failed")


@pytest.mark.asyncio
async def test_get_session_commits_when_block_finishes_normally() -> None:
    """A cleanly finished block leaves its changes committed."""
    session = RecordingSession()
    provider = build_unconfigured_provider()
    provider._session_factory = cast(
        "async_sessionmaker[AsyncSession]",
        RecordingSessionFactory(session),
    )

    async with provider.get_session() as handed_session:
        assert handed_session is session

    assert session.commit_called is True
    assert session.rollback_called is False


@pytest.mark.asyncio
async def test_get_session_rolls_back_when_block_raises() -> None:
    """A raising block has its changes rolled back and the error re-raised."""
    session = RecordingSession()
    provider = build_unconfigured_provider()
    provider._session_factory = cast(
        "async_sessionmaker[AsyncSession]",
        RecordingSessionFactory(session),
    )

    with pytest.raises(ValueError, match="boom"):
        async with provider.get_session():
            raise ValueError("boom")

    assert session.rollback_called is True
    assert session.commit_called is False


@pytest.mark.asyncio
async def test_get_session_rolls_back_when_caller_is_cancelled() -> None:
    """Cancellation is still a failed transaction and must be rolled back."""
    session = RecordingSession()
    provider = build_unconfigured_provider()
    provider._session_factory = cast(
        "async_sessionmaker[AsyncSession]",
        RecordingSessionFactory(session),
    )

    with pytest.raises(asyncio.CancelledError):
        async with provider.get_session():
            raise asyncio.CancelledError

    assert session.rollback_called is True
    assert provider.stats()["transaction.rolled_back"] == 1


@pytest.mark.asyncio
async def test_health_check_reports_healthy_when_select_one_succeeds() -> None:
    """A reachable database answers the minimal probe query."""
    engine = HealthyFakeEngine()
    provider = build_unconfigured_provider()
    provider._engine = cast("AsyncEngine", engine)

    healthy = await provider.health_check()

    assert healthy is True
    assert engine.connection.executed is True
    assert provider.stats()["health.ok"] == 1


@pytest.mark.asyncio
async def test_health_check_reports_unhealthy_when_engine_fails() -> None:
    """A failing engine answers the probe with False, not a crash."""
    provider = build_unconfigured_provider()
    provider._engine = cast("AsyncEngine", BrokenFakeEngine())

    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_failure_and_recovery_are_logged_once(caplog) -> None:
    """Repeated probes must not flood logs, while recovery remains visible."""
    provider = build_unconfigured_provider()
    provider._engine = cast("AsyncEngine", BrokenFakeEngine())

    with caplog.at_level(logging.INFO, logger=session_provider_module.__name__):
        assert await provider.health_check() is False
        assert await provider.health_check() is False
        provider._engine = cast("AsyncEngine", HealthyFakeEngine())
        assert await provider.health_check() is True

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("PostgreSQL health check failed") == 1
    assert messages.count("PostgreSQL connection recovered") == 1
    assert provider.stats()["health.error"] == 2
    assert provider.stats()["health.degraded"] == 0


@pytest.mark.asyncio
async def test_health_check_reports_unhealthy_after_close() -> None:
    """A closed provider answers the probe with False, not a crash."""
    provider = build_unconfigured_provider()
    provider._engine = None
    provider._session_factory = None

    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_close_disposes_engine_and_poisons_later_use() -> None:
    """After shutdown, engine and get_session fail loudly instead of misbehaving."""
    engine = HealthyFakeEngine()
    provider = build_unconfigured_provider()
    provider._engine = cast("AsyncEngine", engine)
    provider._session_factory = cast(
        "async_sessionmaker[AsyncSession]",
        RecordingSessionFactory(RecordingSession()),
    )

    await provider.close()

    assert engine.disposed is True
    with pytest.raises(RuntimeError, match="closed"):
        _ = provider.engine
    with pytest.raises(RuntimeError, match="closed"):
        async with provider.get_session():
            pass


@pytest.mark.asyncio
async def test_close_poisoned_provider_even_when_pool_disposal_fails() -> None:
    """A cleanup failure must not leave a half-closed provider reusable."""
    provider = build_unconfigured_provider()
    provider._engine = cast("AsyncEngine", FailingDisposeEngine())
    provider._session_factory = cast(
        "async_sessionmaker[AsyncSession]",
        RecordingSessionFactory(RecordingSession()),
    )

    with pytest.raises(RuntimeError, match="dispose failed"):
        await provider.close()

    assert provider.stats()["closed"] == 1
    assert provider.stats()["close.error"] == 1
    with pytest.raises(RuntimeError, match="closed"):
        _ = provider.engine
