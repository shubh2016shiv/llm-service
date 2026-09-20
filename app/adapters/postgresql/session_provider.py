"""
PostgreSQL session provider — the pool keeper
==============================================

What this file is for
---------------------
Every database query needs a "connection" — an open phone line to the
PostgreSQL server. Opening a fresh line for every single query is slow
(handshake, authentication, setup). So this file:

    1. Builds ONE pool of ready-made lines at startup (the "engine").
    2. Hands out a fresh, short-lived "session" for each piece of work.
       A session = a checked-out line + the work done on it, wrapped in a
       transaction (an all-or-nothing bundle of changes).
    3. Commits the transaction when the work succeeds, rolls it back when
       it fails — automatically, so callers never have to remember.
    4. Answers "is the database alive?" for the health endpoint.
    5. Closes the whole pool at shutdown, and makes any use-after-close
       fail loudly instead of silently misbehaving.

Who uses this file
------------------
    FastAPI lifespan -> PostgresSessionProvider -> SQLAlchemy/asyncpg pool
                              |
                              `-> app.database persistence classes

One provider and one pool exist per app worker (each process gets its
own). Connections and sessions are never shared between concurrent
operations — each ``get_session`` call gets its own session and its own
transaction.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# logging = writing to the application log.
import logging

# asynccontextmanager = turns get_session into an "async with ..." block,
# so a session's lifetime is tied to a code block and can never be left
# half-open by a caller who forgets to close it.
from collections import defaultdict
from contextlib import asynccontextmanager

# TYPE_CHECKING is only True while a type checker reads the file, never at
# runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

# SQLAlchemy's async toolkit, piece by piece:
#   AsyncEngine        = the pool itself.
#   AsyncSession       = one short-lived line + the work done on it.
#   async_sessionmaker = a factory that stamps out fresh sessions.
#   create_async_engine= builds the pool from settings.
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# text = wrap a raw SQL string (used only for the one-line health check).
from sqlalchemy.sql import text

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from app.core.settings.models import DatabaseConfig

logger = logging.getLogger(__name__)


class PostgresSessionProvider:
    """The pool keeper: owns one PostgreSQL pool and stamps out sessions.

    Think of it as a building's phone switchboard:
      - it wires up the pool of phone lines once, at startup,
      - each caller gets their own private line (session) for the length
        of one piece of work,
      - when the work ends, the switchboard either saves the changes
        (commit) or undoes them (rollback) — the caller never has to
        remember,
      - at closing time it tears the whole switchboard down.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        """Build the pool and the session factory from validated settings.

        Args:
            config: The validated database settings (URL, pool sizes,
                timeouts). The URL is a secret and is never logged.
        """
        # Extra instructions sent to the PostgreSQL server on every
        # connection we open:
        #   statement_timeout = give up on one query after this many
        #                       milliseconds, so a runaway query cannot
        #                       hold a line hostage forever.
        #   timezone          = interpret timestamps as UTC, so all
        #                       workers agree about time.
        #   statement_cache_size = 0 disables asyncpg's prepared-statement
        #                       cache. Deliberate trade-off: the cache can
        #                       trip over SQLAlchemy 2.0's DDL/ALTER
        #                       patterns ("prepared statement already
        #                       exists" errors), so we trade a tiny
        #                       per-query prepare cost for predictability.
        connect_args: dict[str, object] = {
            # ``timeout`` bounds the TCP/authentication handshake. Pool
            # timeout only limits waiting for a free slot; it does not limit
            # the subsequent network connection attempt.
            "timeout": config.database_connect_timeout_seconds,
            # This client-side ceiling complements PostgreSQL's
            # statement_timeout when the network itself stops responding.
            "command_timeout": config.database_statement_timeout_ms / 1000,
            "server_settings": {
                "statement_timeout": str(config.database_statement_timeout_ms),
                "timezone": "UTC",
            },
            "statement_cache_size": 0,
        }
        # Build the pool ("engine"):
        #   pool_size / max_overflow = how many lines may exist at once.
        #   pool_pre_ping = poke a line before using it, so a stale line
        #                   the server silently dropped gets replaced.
        #   pool_recycle  = retire lines older than this many seconds.
        #   pool_timeout  = how long a caller may wait for a free line
        #                   before giving up.
        #   echo=False    = do NOT print every SQL statement to the log.
        self._engine: AsyncEngine | None = create_async_engine(
            config.database_url.get_secret_value(),
            pool_size=config.database_pool_size,
            max_overflow=config.database_max_overflow,
            pool_pre_ping=True,
            pool_recycle=config.database_pool_recycle_seconds,
            pool_timeout=config.database_pool_timeout_seconds,
            connect_args=connect_args,
            echo=False,
        )
        # The session-stamping machine:
        #   expire_on_commit=False = objects stay usable after a commit
        #                           (no surprise re-reads in async code).
        #   autoflush=False        = changes are sent to the database only
        #                           when we say so, never "by magic"
        #                           mid-read.
        self._session_factory: async_sessionmaker[AsyncSession] | None = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self._stats: defaultdict[str, int] = defaultdict(int)
        self._health_degraded = False
        # Log the pool's shape — never the URL (it is a secret).
        logger.info(
            "PostgreSQL connection pool initialized",
            extra={
                "pool_size": config.database_pool_size,
                "max_overflow": config.database_max_overflow,
                "pool_recycle_seconds": config.database_pool_recycle_seconds,
                "statement_timeout_ms": config.database_statement_timeout_ms,
                "connect_timeout_seconds": config.database_connect_timeout_seconds,
            },
        )

    @property
    def engine(self) -> AsyncEngine:
        """Hand over the pool, or refuse loudly if it has been closed.

        The check turns a use-after-shutdown bug into an immediate crash
        instead of a mystery error deep inside a query.
        """
        if self._engine is None:
            raise RuntimeError("PostgresSessionProvider is closed and cannot provide an engine.")
        return self._engine

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Hand out one private session for the length of a code block.

        Usage (the only supported way to get a session):
            async with provider.get_session() as session:
                ... work ...

        What happens automatically, in order:
          - a fresh session and transaction are opened for the block,
          - if the block finishes normally, its changes are COMMITTED,
          - if the block raises, its changes are ROLLED BACK and the
            error keeps travelling upward,
          - if the rollback itself also fails, the caller still sees the
            ORIGINAL error — a broken rollback is logged, never allowed
            to mask the real problem.

        Callers never call commit() or rollback() themselves — that is
        the whole point of this method.
        """
        if self._session_factory is None:
            # Closed already (shutdown). Fail loudly instead of handing
            # out a session that cannot reach the database.
            raise RuntimeError("PostgresSessionProvider is closed and cannot provide sessions.")
        # Stamp out a session for this block. The ``async with`` on the
        # factory guarantees the session is closed on the way out, even
        # if the block raises (or the task is cancelled).
        async with self._session_factory() as session:
            self._stats["session.opened"] += 1
            try:
                yield session  # hand the line to the caller's block
                # The block ended normally -> make its changes permanent.
                await session.commit()
                self._stats["transaction.committed"] += 1
            except BaseException:
                # The block raised -> undo its changes.
                try:
                    await session.rollback()
                    self._stats["transaction.rolled_back"] += 1
                except BaseException:
                    # The rollback itself failed (for example the
                    # connection died). Log it, but do NOT let it replace
                    # the caller's original error.
                    self._stats["transaction.rollback_failed"] += 1
                    logger.exception("PostgreSQL transaction rollback failed")
                raise  # the original error keeps travelling

    async def health_check(self) -> bool:
        """Answer "can PostgreSQL run a query right now?" with True/False.

        Used by the app's readiness endpoint. Runs the smallest possible
        query (SELECT 1, meaning "are you there?") on a fresh pooled
        connection, and never crashes the probe itself.
        """
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            self._stats["health.ok"] += 1
            if self._health_degraded:
                self._health_degraded = False
                logger.info("PostgreSQL connection recovered")
            return True
        except Exception:
            # Any failure (server down, bad credentials, network) ->
            # report unhealthy. The probe must never crash the endpoint.
            self._stats["health.error"] += 1
            if not self._health_degraded:
                self._health_degraded = True
                logger.warning("PostgreSQL health check failed", exc_info=True)
            return False

    def stats(self) -> dict[str, int]:
        """Return transaction and health counters as a detached snapshot."""
        snapshot = dict(sorted(self._stats.items()))
        snapshot["health.degraded"] = int(self._health_degraded)
        snapshot["closed"] = int(self._engine is None)
        return snapshot

    async def close(self) -> None:
        """Tear down the pool at shutdown, and poison any later reuse.

        After this call, engine and get_session raise immediately — a
        request still running during shutdown must not silently keep
        working against a half-closed pool.
        """
        engine = self._engine
        # Poison both handles before awaiting network cleanup. A concurrent
        # late request must fail immediately, and a disposal failure must not
        # leave a half-closed provider reusable.
        self._engine = None
        self._session_factory = None
        if engine is not None:
            # Close every pooled line now. (Lines checked out by
            # in-flight requests are closed as soon as they are returned.)
            try:
                await engine.dispose()
            except Exception:
                self._stats["close.error"] += 1
                logger.exception("PostgreSQL connection pool disposal failed")
                raise
            self._stats["close.ok"] += 1
            logger.info("PostgreSQL connection pool disposed")
