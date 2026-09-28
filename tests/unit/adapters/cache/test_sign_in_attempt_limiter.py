"""Behavioral tests for the Redis-backed sign-in attempt limiter.

Architecture:
    SignInAttemptLimiter -> RedisConnectionManager -> FakeAsyncRedis

The limiter must fail open when Redis becomes unavailable after a client has
already been acquired, while still surfacing application programming errors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest
import redis

from app.adapters.cache.redis_connection import RedisConnectionManager
from app.adapters.cache.sign_in_attempt_limiter import AttemptBudget, SignInAttemptLimiter
from tests.unit.adapters.cache.redis_cache_fakes import FakeAsyncRedis, RecordingClientFactory

REDIS_URL = "redis://fake-host:6379/0"
USERNAME = "alice"
LOGGER_NAME = "app.adapters.cache.sign_in_attempt_limiter"
InstallClient = Callable[[FakeAsyncRedis], RecordingClientFactory]


@pytest.fixture
def install_client(monkeypatch: pytest.MonkeyPatch) -> InstallClient:
    """Route limiter connections to a controlled in-memory Redis client."""

    def install(client: FakeAsyncRedis) -> RecordingClientFactory:
        factory = RecordingClientFactory(client)
        monkeypatch.setattr("app.adapters.cache.redis_connection.aioredis.from_url", factory)
        return factory

    return install


def build_limiter() -> SignInAttemptLimiter:
    """Create a limiter with immediate reconnect eligibility for tests."""
    connection = RedisConnectionManager(
        REDIS_URL,
        initial_retry_delay_seconds=0.0,
        max_retry_delay_seconds=0.0,
    )
    return SignInAttemptLimiter(connection, max_attempts=2, window_seconds=60)


@pytest.mark.asyncio
async def test_limiter_records_checks_and_clears_failed_attempts(
    install_client: InstallClient,
) -> None:
    """A counter reaches its budget and a successful sign-in clears it."""
    # Arrange
    install_client(FakeAsyncRedis())
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act
    await limiter.record_failure(USERNAME)
    await limiter.record_failure(USERNAME)
    exhausted_budget = await limiter.check(USERNAME)
    await limiter.clear(USERNAME)
    cleared_budget = await limiter.check(USERNAME)

    # Assert
    assert exhausted_budget == AttemptBudget(allowed=False, retry_after_seconds=60)
    assert cleared_budget == AttemptBudget(allowed=True, retry_after_seconds=0)


@pytest.mark.asyncio
async def test_record_failure_atomically_increments_and_refreshes_expiry(
    install_client: InstallClient,
) -> None:
    """REQ: each failure updates its counter and sliding expiry in one Redis call."""
    # Arrange
    client = FakeAsyncRedis()
    install_client(client)
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act
    await limiter.record_failure(USERNAME)
    counter_key = next(iter(client.values))
    client.expirations[counter_key] = 5
    await limiter.record_failure(USERNAME)

    # Assert
    assert client.values[counter_key] == b"2"
    assert client.expirations[counter_key] == 60
    assert len(client.eval_calls) == 2
    assert all(call[1] == 1 for call in client.eval_calls)
    assert client.incr_count == 0
    assert client.expire_count == 0


@pytest.mark.asyncio
async def test_check_when_legacy_counter_has_no_expiry_repairs_bounded_lockout(
    install_client: InstallClient,
) -> None:
    """REQ: counters orphaned by older deployments must not lock users forever."""
    # Arrange
    client = FakeAsyncRedis()
    install_client(client)
    limiter = build_limiter()
    await limiter._connection.connect()
    await limiter.record_failure(USERNAME)
    await limiter.record_failure(USERNAME)
    client.expirations.clear()

    # Act
    budget = await limiter.check(USERNAME)

    # Assert
    assert budget == AttemptBudget(allowed=False, retry_after_seconds=60)
    assert list(client.expirations.values()) == [60]
    assert client.expire_count == 1


@pytest.mark.asyncio
async def test_check_when_counter_disappears_before_ttl_lookup_allows_attempt(
    install_client: InstallClient,
) -> None:
    """A naturally expired counter must not create a synthetic lockout window."""
    # Arrange
    client = FakeAsyncRedis(ttl_result=-2)
    install_client(client)
    limiter = build_limiter()
    await limiter._connection.connect()
    await limiter.record_failure(USERNAME)
    await limiter.record_failure(USERNAME)

    # Act
    budget = await limiter.check(USERNAME)

    # Assert
    assert budget == AttemptBudget(allowed=True, retry_after_seconds=0)


@pytest.mark.asyncio
async def test_check_when_counter_is_corrupt_discards_it_without_degrading_redis(
    install_client: InstallClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Corrupt cache data self-heals without being reported as an outage."""
    # Arrange
    client = FakeAsyncRedis()
    install_client(client)
    limiter = build_limiter()
    await limiter._connection.connect()
    await limiter.record_failure(USERNAME)
    counter_key = next(iter(client.values))
    corrupt_value = b"not-an-integer"
    client.values[counter_key] = corrupt_value

    # Act
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        budget = await limiter.check(USERNAME)

    # Assert
    stats = limiter._connection.stats()
    assert budget == AttemptBudget(allowed=True, retry_after_seconds=0)
    assert counter_key not in client.values
    assert stats["signin_limiter_check.corrupt"] == 1
    assert stats.get("signin_limiter_check.error", 0) == 0
    assert stats["degraded"] == 0
    warning_text = " ".join(record.getMessage() for record in caplog.records)
    assert "Corrupt sign-in attempt counter discarded" in warning_text
    assert USERNAME not in warning_text
    assert corrupt_value.decode() not in warning_text


@pytest.mark.asyncio
async def test_check_when_corrupt_counter_delete_fails_reports_backend_error(
    install_client: InstallClient,
) -> None:
    """A Redis failure during corruption cleanup remains an observable outage."""
    # Arrange
    client = FakeAsyncRedis(delete_error=redis.ConnectionError("dropped"))
    install_client(client)
    limiter = build_limiter()
    await limiter._connection.connect()
    await limiter.record_failure(USERNAME)
    counter_key = next(iter(client.values))
    client.values[counter_key] = b"corrupt"

    # Act
    budget = await limiter.check(USERNAME)

    # Assert
    stats = limiter._connection.stats()
    assert budget == AttemptBudget(allowed=True, retry_after_seconds=0)
    assert stats["signin_limiter_check.corrupt"] == 1
    assert stats["signin_limiter_check.error"] == 1
    assert stats["degraded"] == 1


@pytest.mark.asyncio
async def test_record_failure_when_redis_rejects_script_propagates_response_error(
    install_client: InstallClient,
) -> None:
    """Malformed Lua and other command defects must not become silent no-ops."""
    # Arrange
    client = FakeAsyncRedis(command_error=redis.ResponseError("bad script"))
    install_client(client)
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act / Assert
    with pytest.raises(redis.ResponseError, match="bad script"):
        await limiter.record_failure(USERNAME)
    stats = limiter._connection.stats()
    assert stats.get("signin_limiter_record.error", 0) == 0
    assert stats["degraded"] == 0


@pytest.mark.asyncio
async def test_operations_when_redis_is_unavailable_keep_limiter_fail_open(
    install_client: InstallClient,
) -> None:
    """An unavailable client allows checks and makes writes safe no-ops."""
    # Arrange
    install_client(FakeAsyncRedis(ping_errors=99))
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act
    budget = await limiter.check(USERNAME)
    await limiter.record_failure(USERNAME)
    await limiter.clear(USERNAME)

    # Assert
    assert budget == AttemptBudget(allowed=True, retry_after_seconds=0)


@pytest.mark.asyncio
async def test_check_when_redis_times_out_returns_allowed_budget(
    install_client: InstallClient,
) -> None:
    """REQ: a mid-operation Redis timeout must not prevent sign-in."""
    # Arrange
    install_client(FakeAsyncRedis(command_error=redis.TimeoutError("slow")))
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act
    budget = await limiter.check(USERNAME)

    # Assert
    assert budget == AttemptBudget(allowed=True, retry_after_seconds=0)
    assert limiter._connection.stats()["signin_limiter_check.error"] == 1
    assert limiter._connection.stats()["degraded"] == 1
    assert limiter._connection.stats()["connected"] == 0


@pytest.mark.asyncio
async def test_record_failure_when_redis_disconnects_does_not_raise(
    install_client: InstallClient,
) -> None:
    """REQ: failure accounting becomes a no-op during a Redis outage."""
    # Arrange
    install_client(FakeAsyncRedis(command_error=redis.ConnectionError("dropped")))
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act
    await limiter.record_failure(USERNAME)

    # Assert
    assert limiter._connection.stats()["signin_limiter_record.error"] == 1
    assert limiter._connection.stats()["degraded"] == 1
    assert limiter._connection.stats()["connected"] == 0


@pytest.mark.asyncio
async def test_clear_when_redis_disconnects_does_not_raise(
    install_client: InstallClient,
) -> None:
    """REQ: clearing a counter becomes a no-op during a Redis outage."""
    # Arrange
    install_client(FakeAsyncRedis(command_error=redis.ConnectionError("dropped")))
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act
    await limiter.clear(USERNAME)

    # Assert
    assert limiter._connection.stats()["signin_limiter_clear.error"] == 1
    assert limiter._connection.stats()["degraded"] == 1
    assert limiter._connection.stats()["connected"] == 0


@pytest.mark.asyncio
async def test_check_when_client_has_programming_error_propagates_error(
    install_client: InstallClient,
) -> None:
    """Caller defects must not be misreported as an available sign-in budget."""
    # Arrange
    install_client(FakeAsyncRedis(command_error=TypeError("key must be str")))
    limiter = build_limiter()
    await limiter._connection.connect()

    # Act / Assert
    with pytest.raises(TypeError, match="key must be str"):
        await limiter.check(USERNAME)


@pytest.mark.parametrize(
    ("max_attempts", "window_seconds", "message"),
    [
        (0, 60, "max_attempts must be at least 1"),
        (2, 0, "window_seconds must be at least 1"),
    ],
)
def test_init_when_budget_is_invalid_raises_value_error(
    max_attempts: int,
    window_seconds: int,
    message: str,
) -> None:
    """Limiter budgets must contain positive attempt and window values."""
    # Arrange
    connection = RedisConnectionManager(REDIS_URL)

    # Act / Assert
    with pytest.raises(ValueError, match=message):
        SignInAttemptLimiter(
            connection,
            max_attempts=max_attempts,
            window_seconds=window_seconds,
        )
