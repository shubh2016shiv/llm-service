"""Redis-backed failed sign-in counter.

Architecture:
    SignInService -> SignInAttemptLimiter -> RedisConnectionManager -> Redis

Why this exists
---------------
``/api/v1/auth/sign-in`` is the one unauthenticated endpoint that accepts a
secret, which makes it the natural target for password guessing. Without a
budget, an attacker can try passwords as fast as the network allows.

Only FAILURES are counted, and a success clears the counter, so a person typing
one wrong password then the right one is never delayed.

Fail-open, deliberately
-----------------------
When Redis is unreachable this limiter allows the attempt rather than refusing
it. Redis is a cache in this system, not a source of truth, and the rest of the
service degrades to PostgreSQL when it is down. Failing closed here would turn
a cache outage into a total sign-in outage — a self-inflicted denial of
service. The trade is explicit: during a Redis outage the rate limit is absent,
while password verification, account status, and token signing all still apply.

Dependencies:
    - app.adapters.cache.redis_connection — supplies the shared client.

Author: Shubham Singh
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from app.adapters.cache.redis_connection import RedisConnectionManager

logger = logging.getLogger(__name__)

_KEY_PREFIX = "signin:fail"


class AttemptBudget(NamedTuple):
    """The limiter's verdict for one sign-in attempt."""

    allowed: bool
    retry_after_seconds: int


class SignInAttemptLimiter:
    """Count failed sign-ins per username and refuse once the budget is spent.

    Does X given Y: given a username, reports whether another attempt is
    allowed, and records failures. Does not verify passwords (that belongs in
    ``password_hashing``) and does not decide the HTTP status (that belongs in
    ``exception_handlers``).

    Example:
        >>> limiter = SignInAttemptLimiter(connection, max_attempts=5, window_seconds=300)
        >>> budget = await limiter.check("alice")
        >>> budget.allowed
        True
    """

    def __init__(
        self,
        connection: RedisConnectionManager,
        *,
        max_attempts: int,
        window_seconds: int,
    ) -> None:
        """Initialize with the shared Redis connection and the attempt budget.

        Args:
            connection: Shared Redis connection manager owned by the app lifespan.
            max_attempts: Failures tolerated inside one window.
            window_seconds: Length of the window, and of the resulting lockout.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self._connection = connection
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds

    async def check(self, username: str) -> AttemptBudget:
        """Report whether this username may attempt a sign-in right now.

        Args:
            username: Username the caller is attempting to sign in as.

        Returns:
            AttemptBudget: allowed=False with a positive retry hint once the
            failure count has reached the configured maximum.
        """
        client = await self._connection.acquire()
        if client is None:
            return AttemptBudget(allowed=True, retry_after_seconds=0)
        key = self._key(username)
        try:
            raw_count = await client.get(key)
            if raw_count is None or int(raw_count) < self._max_attempts:
                return AttemptBudget(allowed=True, retry_after_seconds=0)
            # A key can lose its TTL only through operator intervention; treat
            # a missing one as a full window rather than reporting "retry in
            # -1 seconds" to the caller.
            remaining_ttl = await client.ttl(key)
            retry_after = remaining_ttl if remaining_ttl > 0 else self._window_seconds
            return AttemptBudget(allowed=False, retry_after_seconds=int(retry_after))
        except (ValueError, OSError) as exc:
            self._connection.handle_error("signin_limiter_check", exc)
            return AttemptBudget(allowed=True, retry_after_seconds=0)

    async def record_failure(self, username: str) -> None:
        """Count one failed attempt, starting the window if this is the first."""
        client = await self._connection.acquire()
        if client is None:
            return
        key = self._key(username)
        try:
            # The expiry is set on every failure, so a run of attempts extends
            # the lockout instead of letting it lapse mid-attack. A sliding
            # window is the point: it is what makes sustained guessing futile.
            attempt_count = await client.incr(key)
            await client.expire(key, self._window_seconds)
            logger.warning(
                "Failed sign-in recorded",
                extra={
                    "attempt_count": int(attempt_count),
                    "max_attempts": self._max_attempts,
                    "window_seconds": self._window_seconds,
                },
            )
        except (ValueError, OSError) as exc:
            self._connection.handle_error("signin_limiter_record", exc)

    async def clear(self, username: str) -> None:
        """Forget past failures after a successful sign-in."""
        client = await self._connection.acquire()
        if client is None:
            return
        try:
            await client.delete(self._key(username))
        except (ValueError, OSError) as exc:
            self._connection.handle_error("signin_limiter_clear", exc)

    @staticmethod
    def _key(username: str) -> str:
        """Build the counter key from a hash of the username.

        The username is hashed rather than embedded: it is unauthenticated
        input, so using it raw would let a caller inject separators into key
        names, and would leave attempted account names sitting in Redis where
        anyone with cache access could read them.
        """
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
        return f"{_KEY_PREFIX}:{digest}"
