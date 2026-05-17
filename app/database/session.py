"""
Database Session Manager
------------------------
Singleton async engine + session factory built on SQLAlchemy 2.0 with asyncpg.

Why asyncpg?
  asyncpg is a pure-Python async PostgreSQL driver with no DBAPI layer in the
  hot path. It speaks the PostgreSQL wire protocol directly from native coroutines,
  making it the fastest and most resource-efficient option for high-concurrency
  Python services. SQLAlchemy 2.0's async layer wraps asyncpg without adding
  meaningful overhead, keeping the enterprise ORM abstraction while preserving
  asyncpg's performance characteristics.

Pool strategy:
  The QueuePool (SQLAlchemy default for async) keeps `pool_size` connections
  warm and allows up to `max_overflow` extra connections under burst load.
  `pool_pre_ping` detects broken connections before they reach application code,
  preventing silent failures after cloud NAT resets or DB failovers.
  `pool_recycle` forces connections to be re-established before the PostgreSQL
  server-side idle timeout would close them, eliminating surprise EOF errors
  during long idle periods.

Statement safety:
  asyncpg `connect_args` pass a server-side `statement_timeout` (in ms) via
  PostgreSQL GUC so runaway queries are killed at the DB, not the app. This is
  the correct place for this guard: it applies to every statement regardless of
  which code path issues it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings.settings import get_application_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# asyncpg-specific pool configuration defaults. These supplement the values
# read from ApplicationSettings (pool_size, max_overflow) and are kept here
# rather than in ApplicationSettings because they are internal infrastructure
# concerns, not operator configuration knobs.
_POOL_RECYCLE_SECONDS: int = 1800  # Recycle connections after 30 min
_POOL_TIMEOUT_SECONDS: int = 30  # Max wait for a connection from pool
_STATEMENT_TIMEOUT_MS: int = 60_000  # Kill any statement after 60 s


class DatabaseSessionManager:
    """Singleton async database session manager backed by asyncpg.

    Instantiate freely — every call to DatabaseSessionManager() returns the
    same object, so only one connection pool exists per process.

    Typical usage:
        manager = DatabaseSessionManager()
        async with manager.get_session() as session:
            result = await session.execute(text("SELECT 1"))

    Lifecycle:
        Call `await manager.close()` during application shutdown to gracefully
        drain the pool and release all PostgreSQL server-side resources.
    """

    _instance: ClassVar[DatabaseSessionManager | None] = None
    _engine: AsyncEngine | None
    _session_factory: async_sessionmaker[AsyncSession] | None
    _initialized: bool

    # -------------------------------------------------------------------------
    # Singleton construction
    # -------------------------------------------------------------------------

    def __new__(cls) -> DatabaseSessionManager:
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._engine = None
            instance._session_factory = None
            instance._initialized = False
            cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        settings = get_application_settings()
        database_url = settings.database_url.get_secret_value()

        # asyncpg connect_args are forwarded verbatim to asyncpg.connect().
        # server_settings injects PostgreSQL GUC values at connection time so
        # they apply to every statement without requiring explicit SET commands.
        connect_args: dict = {
            "server_settings": {
                # Kill runaway queries at the database layer.
                "statement_timeout": str(_STATEMENT_TIMEOUT_MS),
                # Ensure the client and server agree on timezone.
                "timezone": "UTC",
            },
        }

        self._engine = create_async_engine(
            database_url,
            # Core pool sizing — read from ApplicationSettings so operators
            # can tune these via environment variables.
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            # Emit a SELECT 1 before returning a recycled connection to the
            # caller. Adds ~0.1 ms per checkout but prevents silent failures
            # after cloud NAT resets or DB-side idle connection kills.
            pool_pre_ping=True,
            # Force connection recycling before PostgreSQL's server-side idle
            # timeout. Prevents EOF errors during long low-traffic periods.
            pool_recycle=_POOL_RECYCLE_SECONDS,
            # Maximum seconds a checkout blocks waiting for a free connection.
            # Raising HTTPException(503) is better than hanging indefinitely.
            pool_timeout=_POOL_TIMEOUT_SECONDS,
            # asyncpg-specific settings forwarded at connection time.
            connect_args=connect_args,
            # Never echo SQL in the engine itself; use SQLAlchemy event hooks
            # for query logging when needed in development.
            echo=False,
        )

        # async_sessionmaker is the SQLAlchemy 2.0 recommended factory.
        # expire_on_commit=False prevents implicit lazy-loads on committed ORM
        # objects, which would raise MissingGreenlet in async contexts.
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        self._initialized = True
        logger.info(
            "DatabaseSessionManager ready — asyncpg pool initialised "
            f"(pool_size={settings.database_pool_size}, "
            f"max_overflow={settings.database_max_overflow}, "
            f"pool_recycle={_POOL_RECYCLE_SECONDS}s, "
            f"statement_timeout={_STATEMENT_TIMEOUT_MS}ms)"
        )

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        """The underlying AsyncEngine. Never None after __init__."""
        if self._engine is None:
            raise RuntimeError(
                "DatabaseSessionManager engine is not initialised. "
                "This should not happen — check startup order."
            )
        return self._engine

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a fully managed AsyncSession.

        Transaction lifecycle:
          - On normal exit: commits the transaction.
          - On any exception: rolls back the transaction and re-raises.

        All persistence classes call this via BasePersistence.get_session().
        Never call session.commit() or session.rollback() inside a `with` block
        returned by this method — the manager owns the transaction boundary.

        Yields:
            AsyncSession: A live, transaction-wrapped SQLAlchemy async session.

        Raises:
            RuntimeError: If the manager has not been initialised.
            sqlalchemy.exc.SQLAlchemyError: On database-level failures.
        """
        if self._session_factory is None:
            raise RuntimeError(
                "DatabaseSessionManager is not initialised. "
                "Ensure it is instantiated before handling requests."
            )

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        """Dispose the engine, drain the pool, and reset the singleton.

        Call once during application shutdown (e.g., FastAPI lifespan teardown).
        After this call the manager is unusable until re-instantiated.
        """
        if self._engine is not None:
            await self._engine.dispose()
            logger.info("DatabaseSessionManager: engine disposed, pool drained.")

        self._engine = None
        self._session_factory = None
        self._initialized = False
        DatabaseSessionManager._instance = None
