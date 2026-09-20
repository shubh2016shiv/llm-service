"""
Redis connection manager
========================

What this file is for (in one paragraph)
----------------------------------------
Our app stores cached data in Redis, a very fast memory-based storage that
every running copy of the app shares. Talking to Redis needs a "connection"
(a phone line). This file takes care of that phone line: dialing, hanging
up, redialing, and remembering whether the line currently works.

It does NOT know WHAT data we store (that is redis_cache.py's job). It only
answers one question over and over: "Can we talk to Redis right now — and
if not, when should we try again?"

The rules of the house
----------------------
1. Never crash. If Redis does not answer, this code logs one warning and
   politely answers "no connection right now" until things improve.
2. Wait before redialing. After a failed dial we wait a little. After
   another failure we wait twice as long — up to a limit. This
   "wait, then wait longer" rule stops a dead Redis from slowing down
   every single request with pointless dialing.
3. Heal by itself. The moment a dial succeeds again, normal work resumes.
   Nobody has to restart the program.
4. Keep a tally. Every outcome is counted (dial success, dial failure,
   each failed operation, each recovery). The tally book is readable via
   stats(), so an operator can see at a glance whether the cache is
   working or quietly broken.

Why this logic lives in its own file
------------------------------------
"Which Redis commands do we need?" (redis_cache.py) and "What do we do
while Redis is down?" (this file) are two different jobs that change for
different reasons. Keeping them apart makes each file smaller and easier
to test on its own.

Architecture (who calls whom):
    RedisCache -----.                         .-> redis.asyncio
    RedisPubSub ----> RedisConnectionManager -|
    CircuitBreaker -'                         '-> redis (sync bridge)

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string, so hints can mention
# classes before they are imported. (Standard modern-Python boilerplate.)
from __future__ import annotations

# asyncio    = the machinery for doing many things at once (async/await).
# contextlib = helpers like suppress(...) = "ignore this specific error here".
# logging    = writing to the application log.
# threading  = locks that work between threads (not only between async tasks).
# time       = a clock for the "wait, then wait longer" rule.
import asyncio
import contextlib
import logging
import random
import threading
import time

# defaultdict = a dict where a missing key automatically reads as 0.
# Final       = marks a value as "constant — never change me".
from collections import defaultdict
from typing import Final

# The Redis library, in two flavors:
#   redis          -> the OLD-style client: every command blocks (freezes the
#                     program) until the answer arrives.
#   redis.asyncio  -> the NEW-style client: every command can be awaited, so
#                     the program does other work while waiting for answers.
#
# This import is plain and eager (not hidden in a try/except) because Redis
# is a required part of this app, declared in pyproject.toml. The error
# lists below need the real Redis exception classes, and static type
# checking works better with a normal import.
import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# The two groups of errors this file cares about
# ─────────────────────────────────────────────────────────────────────────

# Group 1 — errors that mean "Redis itself failed": it is down, the network
# broke, or the reply never arrived. These are normal life, NOT bugs in our
# code, so we catch them and keep serving without the cache.
#
# Group 2 is everything else: TypeError, ValueError, AttributeError, and
# friends. Those mean WE made a mistake in our own code. We must NOT catch
# them, because swallowing them would hide real bugs and report them as
# ordinary "cache miss" lies.
BACKEND_ERRORS: Final = (redis.RedisError, OSError)

# A smaller list inside Group 1: errors that mean "this particular
# connection is now useless" — the plug was pulled. These force us to drop
# the connection and dial again later.
#
# Deliberately NOT in this list: redis.ResponseError ("Redis answered, but
# the command was wrong"). A wrong command is OUR bug, not a reason to
# throw away a healthy connection.
_CONNECTION_ERRORS: Final = (redis.ConnectionError, redis.TimeoutError, OSError)

# ─────────────────────────────────────────────────────────────────────────
# Default numbers
# ─────────────────────────────────────────────────────────────────────────

# These are used only when a caller builds this class without providing
# better numbers (tests, small scripts). The real app always passes its own
# values from the settings file (CacheConfig) at startup.
DEFAULT_INITIAL_RETRY_DELAY_SECONDS: Final = 1.0  # first redial waits 1 second
DEFAULT_MAX_RETRY_DELAY_SECONDS: Final = 30.0  # never wait longer than 30 seconds
DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS: Final = 5  # give up one dial after 5 seconds
DEFAULT_SOCKET_TIMEOUT_SECONDS: Final = 5  # give up one Redis command after 5 seconds
DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS: Final = 30  # check idle lines every 30 seconds


class RedisConnectionManager:
    """The caretaker of our connection to Redis.

    Think of it as a phone operator that:
      - holds the current phone line (the "client" object),
      - remembers whether we are connected, disconnected, or shut down,
      - decides when it is allowed to redial after a failure,
      - keeps the tally of everything that happened.

    Example:
        >>> connection = RedisConnectionManager("redis://localhost:6379/0")
        >>> client = await connection.acquire(force_retry=True)
        >>> client is None  # True only while Redis is unreachable
        False
    """

    def __init__(
        self,
        redis_url: str,
        *,
        initial_retry_delay_seconds: float = DEFAULT_INITIAL_RETRY_DELAY_SECONDS,
        max_retry_delay_seconds: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
        socket_connect_timeout_seconds: int = DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS,
        socket_timeout_seconds: int = DEFAULT_SOCKET_TIMEOUT_SECONDS,
        health_check_interval_seconds: int = DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
    ) -> None:
        """Store the settings and start in a "not connected" state.

        Nothing is dialed here. The first real dial happens when someone
        calls acquire().

        Args:
            redis_url: The address of Redis, including credentials when
                required (for example "redis://user:secret@host:6379/0").
            initial_retry_delay_seconds: How long to wait before the first
                redial after a failure. Redials happen lazily, inside normal
                Redis operations, so this also limits how often a dead
                Redis gets poked.
            max_retry_delay_seconds: The longest we will ever wait. The wait
                doubles after every failed dial but stops growing here.
            socket_connect_timeout_seconds: How long ONE dialing attempt may
                take before we give up on it.
            socket_timeout_seconds: How long an individual Redis command may
                wait for a response before it fails.
            health_check_interval_seconds: How often the Redis library
                checks that an idle connection is still alive.
        """
        self._redis_url = redis_url  # the address to dial (may hold a password!)
        # The modern (async) client. None means "no connection right now".
        self._redis: aioredis.Redis[bytes] | None = None
        # The old-style (blocking) client. Created only if some library asks.
        self._sync_redis: redis.Redis[bytes] | None = None
        # Three on/off flags that describe our current situation:
        self._connected = False  # are we connected to Redis right now?
        self._degraded = False  # have we already logged "Redis is down"?
        self._closed = False  # did we shut down on purpose (end of program)?
        # A door lock so only ONE dialing attempt happens at a time. Without
        # it, ten simultaneous requests would each dial Redis separately.
        self._connect_lock = asyncio.Lock()
        # A second lock, but for the old-style client. That client is shared
        # with plain (non-async) library code that can run on any thread, so
        # it needs a lock that works across threads, not just across tasks.
        self._sync_client_lock = threading.Lock()
        # Remember the retry settings...
        self.initial_retry_delay_seconds = initial_retry_delay_seconds
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self._socket_connect_timeout_seconds = socket_connect_timeout_seconds
        self._socket_timeout_seconds = socket_timeout_seconds
        self._health_check_interval_seconds = health_check_interval_seconds
        # ...and start the waiting game at its shortest wait.
        self._retry_delay_seconds = initial_retry_delay_seconds
        self._next_retry_at = 0.0  # clock time when the next dial is allowed
        # The tally book: a dict where any missing key automatically reads
        # as 0, so we can add to a tally without creating it first.
        self._stats: defaultdict[str, int] = defaultdict(int)

    @property
    def is_closed(self) -> bool:
        """True when the app has shut this manager down for good.

        Read-only convenience flag so other code can ask "are we shut down?"
        without touching the private _closed variable directly.
        """
        return self._closed

    def reopen(self) -> None:
        """Undo a previous close(), allowing connection attempts again.

        Used by connect() so the app can reactivate the cache after a
        shutdown (for example in tests that stop and start the service).
        """
        self._closed = False

    async def connect(self) -> None:
        """Open the shared Redis connection, tolerating an unavailable backend."""
        self.reopen()
        await self.acquire(force_retry=True)

    async def health_check(self) -> bool:
        """Return whether Redis responds, reconnecting immediately when necessary."""
        client = await self.acquire(force_retry=True)
        if client is None:
            return False
        try:
            await client.ping()
        except BACKEND_ERRORS as error:
            self.handle_error("health_check", error)
            return False
        self.mark_healthy()
        return True

    async def acquire(self, *, force_retry: bool = False) -> aioredis.Redis[bytes] | None:
        """Hand back a working Redis client, or None if we can't right now.

        This is the ONLY place that dials Redis. Every Redis adapter operation
        (get/set/delete/...) calls this first and checks the answer:
          - a client  -> go ahead, use it.
          - None      -> Redis is not reachable right now; return the safe
                         "not found / didn't work" answer instead.

        Args:
            force_retry: Normally, after a failure, we agree to wait a while
                before dialing again (the "wait, then wait longer" rule).
                With force_retry=True we ignore that rule and dial right
                now. The health check uses this: when Redis comes back, the
                regular health-check rhythm — not a restart of the whole
                app — is what discovers it.

        Returns:
            A usable client, or None while Redis is unreachable.
        """
        # Shut down on purpose? Then never dial again.
        if self._closed:
            return None
        # Happy path: we are already connected. Just hand over the client —
        # no dialing, no waiting, almost zero cost.
        if self._connected and self._redis is not None:
            return self._redis
        # Not connected. If the clock says "not yet time to redial", answer
        # "not available" without even trying. This is what keeps a dead
        # Redis from slowing down EVERY request with a doomed dial.
        if not force_retry and time.monotonic() < self._next_retry_at:
            return None
        # Only one caller may dial at a time; everyone else waits at this
        # door and goes in one by one.
        async with self._connect_lock:
            # Someone else may have connected while we waited for the door.
            # Check again — a second dial would abandon their good line.
            if self._connected and self._redis is not None:
                return self._redis
            # Same "not yet time" check, repeated inside the door because
            # time passed while we were waiting for the lock.
            if self._closed or (not force_retry and time.monotonic() < self._next_retry_at):
                return None
            # Time to dial for real.
            return await self._open_connection()

    async def close(self) -> None:
        """Hang up everything, for good. Used once, when the app stops.

        After this, no new dial ever happens — unless reopen() is called.
        That is deliberate: a request that is still running while the app
        shuts down must NOT secretly dial Redis again and undo our cleanup.
        """
        self._closed = True  # remember: we are done
        await self._close_async_client()  # hang up the modern client
        self._discard_sync_client()  # hang up the old-style client
        self._connected = False  # update the flag

    def get_sync_client(self) -> redis.Redis[bytes] | None:
        """Return the old-style (blocking) Redis client, or None if not connected.

        Why this exists at all:
            Most of our code uses the modern async client (commands can be
            awaited). But one library we use — aiobreaker, the circuit
            breaker — only knows the old-style blocking client, so we keep
            a second, separate client just for it.

            "Blocking" means: while that client waits for Redis's answer,
            the program freezes and cannot do anything else. That is why we
            do NOT use this client for our own cache commands.

        Why there is no health ping here:
            A blocking ping would freeze the whole program for up to the
            connection timeout (5 seconds by default) — worse than the
            problem it would solve. If this client turns out to be broken,
            the circuit breaker library has its own plan B: it falls back
            to "circuit open" (stop calling the provider until better news).

        Returns:
            The shared old-style client, or None while disconnected — so the
            caller degrades politely instead of handling a crash that no
            other method here would throw.
        """
        # No connection (or shut down)? Then no old-style client either.
        if self._closed or not self._connected:
            return None
        # Create the client exactly once, even if two threads ask at the
        # same moment (that is why this uses a cross-thread lock).
        with self._sync_client_lock:
            if self._sync_redis is None:
                self._sync_redis = redis.Redis.from_url(
                    self._redis_url,
                    decode_responses=False,  # give us raw bytes, not text
                    socket_connect_timeout=self._socket_connect_timeout_seconds,
                    socket_timeout=self._socket_timeout_seconds,
                    socket_keepalive=True,  # keep idle lines open
                )
            return self._sync_redis

    def _discard_sync_client(self) -> None:
        """Hang up the old-style client so an outage can't leave stale lines.

        Callers that already hold a reference to the old client keep
        working: the Redis library lazily reconnects on its next command,
        and after a successful reconnection this manager builds a fresh
        client anyway.
        """
        # Only touch the client while holding the same lock as creation.
        with self._sync_client_lock:
            if self._sync_redis is not None:
                # Ignore errors while closing — we are throwing it away.
                with contextlib.suppress(*BACKEND_ERRORS):
                    self._sync_redis.close()
                self._sync_redis = None

    def handle_error(self, operation: str, error: BaseException) -> None:
        """Record one failed Redis operation and react to it.

        Called by redis_cache.py every time a Redis command fails.

        Two things can happen:
          1. If the error means "the connection itself is broken" (the plug
             was pulled), mark ourselves disconnected, pick the next
             allowed redial time, and hang up the old-style client too.
          2. Log ONE warning the first time we enter the unhappy
             ("degraded") state — not one per failed request, otherwise a
             long outage would flood the log file.

        Errors during shutdown are ignored entirely: hanging up naturally
        makes in-flight commands fail, and that is us tidying up, not an
        outage to warn about.
        """
        if self._closed:  # shutting down? nothing to record or repair.
            return
        self.count(operation, "error")  # add 1 to the "<operation>.error" tally
        if isinstance(error, _CONNECTION_ERRORS):  # is the connection dead?
            self._connected = False  # yes — we are no longer connected
            self._schedule_retry()  # pick the next allowed redial time
            self._discard_sync_client()  # hang up the old-style client too
        self._mark_degraded(operation, error)  # log the single warning

    def mark_healthy(self) -> None:
        """Announce the happy news exactly once, when Redis comes back.

        Does nothing unless we were in the unhappy ("degraded") state, so a
        routine health check during normal operation stays silent.
        """
        if not self._degraded:  # already healthy? nothing to announce.
            return
        self._degraded = False  # we are back to normal
        self._stats["recovery.transitions"] += 1  # count this recovery
        logger.info("Redis connection recovered; adapter operations resumed")

    def count(self, operation: str, outcome: str, amount: int = 1) -> None:
        """Add ``amount`` to one tally named ``<operation>.<outcome>``.

        For example, count("get", "hit") adds 1 to the "get.hit" tally.
        amount=0 is allowed and does nothing (handy for loops that count
        items and may find zero of them).
        """
        if amount:
            self._stats[f"{operation}.{outcome}"] += amount

    def stats(self) -> dict[str, int]:
        """Return a fresh copy of the tally book, plus two on/off flags.

        Returns a NEW dict (not the live book), so callers can read it
        without worrying about us writing to it at the same time.

        The two flags:
          - "connected": 1 when we are connected to Redis right now, else 0.
          - "degraded":  1 when we are in the "Redis is down" unhappy
            state, else 0.

        Why the tallies exist: a working cache and a broken cache return
        the exact same answers to the rest of the app (by design — that is
        what keeps the app running through an outage). These counters are
        the only way an operator can tell the two situations apart.
        """
        snapshot = dict(sorted(self._stats.items()))  # copy, sorted by name
        snapshot["connected"] = int(self._connected)  # True/False -> 1/0
        snapshot["degraded"] = int(self._degraded)
        return snapshot

    async def _open_connection(self) -> aioredis.Redis[bytes] | None:
        """Dial Redis and check it answers. Keep the line, or plan a redial.

        This is the actual dialing step, run only while holding the connect
        lock. Returns the new client on success, or None when Redis does
        not answer.
        """
        # Hang up any old broken line first, so a fresh dial cannot leak it.
        await self._close_async_client()
        # Build the new client object...
        client = aioredis.from_url(
            self._redis_url,
            decode_responses=False,  # keep values as raw bytes
            socket_connect_timeout=self._socket_connect_timeout_seconds,
            socket_timeout=self._socket_timeout_seconds,
            socket_keepalive=True,  # keep idle lines open
            health_check_interval=self._health_check_interval_seconds,
        )
        try:
            await client.ping()  # "are you there?" — the real test
        except BACKEND_ERRORS as error:
            # No answer. Record it, plan the next redial, log one warning.
            self.count("connect", "error")
            self._schedule_retry()
            self._mark_degraded("connect", error)
            # Throw the failed client away (ignore errors while doing so).
            with contextlib.suppress(*BACKEND_ERRORS):
                await client.aclose()  # type: ignore[attr-defined]
            return None
        # It answered! Make this client the official one.
        return self._adopt_connection(client)

    def _adopt_connection(self, client: aioredis.Redis[bytes]) -> aioredis.Redis[bytes]:
        """Keep the client that just proved it works, and reset the wait timer.

        Called only after a successful ping. From now on, acquire() hands
        out this client instead of dialing again.
        """
        self._redis = client  # remember the good line
        self._connected = True  # we are connected
        # Restart the waiting game from its shortest wait.
        self._retry_delay_seconds = self.initial_retry_delay_seconds
        self._next_retry_at = 0.0  # no pending "not yet" time
        self.count("connect", "ok")  # tally one successful dial
        self.mark_healthy()  # announce recovery if we were degraded
        logger.info("Redis connection established")
        return client

    async def _close_async_client(self) -> None:
        """Hang up the current modern client (if any).

        Called before dialing a fresh connection, so an old broken line is
        never left dangling.
        """
        if self._redis is None:  # nothing to hang up
            return
        # Errors while hanging up are fine — we are discarding it anyway.
        with contextlib.suppress(*BACKEND_ERRORS):
            # Note: the type-checking library still expects close(), but
            # redis-py 5 renamed it to aclose() for async clients. The
            # method really exists at runtime, so this is safe.
            await self._redis.aclose()  # type: ignore[attr-defined]
        self._redis = None

    def _schedule_retry(self) -> None:
        """Move the next allowed dial into the future, and double the wait.

        First failure -> wait 1s, then 2s, 4s, 8s, ... up to the ceiling
        (30s by default). This "wait, then wait longer" rule is what stops
        a dead Redis from turning every request into a slow, doomed dial.
        """
        # Spread replicas across the latter half of the retry window so they
        # do not all reconnect at the same instant after an outage.
        retry_delay_seconds = random.uniform(
            self._retry_delay_seconds / 2,
            self._retry_delay_seconds,
        )
        self._next_retry_at = time.monotonic() + retry_delay_seconds
        # Double the wait for the attempt after that...
        self._retry_delay_seconds = min(
            self._retry_delay_seconds * 2,
            self.max_retry_delay_seconds,  # ...but never above the ceiling
        )

    def _mark_degraded(self, operation: str, error: BaseException) -> None:
        """Log ONE warning the first time we enter the "Redis is down" state.

        The _degraded flag keeps later failures quiet, so an hour-long
        outage produces one log line, not thousands.
        """
        if self._degraded:  # already warned? stay quiet.
            return
        self._degraded = True
        self._stats["degradation.transitions"] += 1
        logger.warning(
            "Redis connection degraded; adapter operations now return fallback values",
            extra={"operation": operation, "error_type": type(error).__name__},
        )
