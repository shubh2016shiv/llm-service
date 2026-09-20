"""Behavioral tests for the Redis cache adapter.

Specification under test is the module contract in ``redis_cache.py``:
    1. Backend failures degrade to documented fallback values, never raise.
    2. Caller defects (wrong argument types) are not disguised as cache misses.
    3. Degradation is temporary -- the adapter reconnects on its own.
    4. Degradation is counted and its transitions logged, so an operator can
       tell a healthy cache from one failing every call.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing

import pytest
import redis

from app.adapters.cache import redis_connection as redis_connection_module
from app.adapters.cache.channels import CONFIG_CHANGES_CHANNEL
from app.adapters.cache.redis_cache import RedisCache
from app.adapters.cache.redis_connection import RedisConnectionManager
from app.adapters.cache.redis_pubsub import RedisPubSub
from tests.unit.adapters.cache.redis_cache_fakes import (
    FakeAsyncRedis,
    FakePubSub,
    RecordingClientFactory,
    build_message_frame,
    build_subscribe_confirmation_frame,
)

REDIS_URL = "redis://fake-host:6379/0"
CACHE_KEY = "deployment:config:demo"
LOGGER_NAME = "app.adapters.cache.redis_connection"


@pytest.fixture
def install_client(monkeypatch):
    """Return a helper that routes RedisCache connections to a fake client."""

    def install(client: FakeAsyncRedis) -> RecordingClientFactory:
        factory = RecordingClientFactory(client)
        monkeypatch.setattr(redis_connection_module.aioredis, "from_url", factory)
        return factory

    return install


def build_cache(retry_delay_seconds: float = 0.0) -> RedisCache:
    """Create an adapter whose reconnect backoff is controlled by the test."""
    connection = RedisConnectionManager(
        REDIS_URL,
        initial_retry_delay_seconds=retry_delay_seconds,
        max_retry_delay_seconds=retry_delay_seconds,
    )
    return RedisCache(connection)


async def collect_payloads(pubsub: RedisPubSub, expected_count: int) -> list[str]:
    """Consume a subscription until the expected payload count arrives."""
    payloads: list[str] = []

    async def consume() -> None:
        async with aclosing(pubsub.subscribe(CONFIG_CHANGES_CHANNEL)) as stream:
            async for payload in stream:
                payloads.append(payload)
                if len(payloads) >= expected_count:
                    return

    await asyncio.wait_for(consume(), timeout=2.0)
    return payloads


async def wait_for_pubsub_handle(client: FakeAsyncRedis) -> FakePubSub:
    """Poll until the adapter has created its first pub/sub handle."""
    for _ in range(100):
        if client.pubsub_handles:
            return client.pubsub_handles[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no pub/sub handle was created")


# ── Connection lifecycle and self-healing ────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_marks_cache_connected_and_counts_success(install_client) -> None:
    """A reachable Redis leaves the adapter connected and not degraded."""
    factory = install_client(FakeAsyncRedis())
    cache = build_cache()

    await cache._connection.connect()

    assert cache._connection.stats()["connected"] == 1
    assert cache._connection.stats()["degraded"] == 0
    assert cache._connection.stats()["connect.ok"] == 1
    assert factory.received_kwargs[0]["socket_timeout"] == 5


@pytest.mark.asyncio
async def test_connect_when_redis_is_unreachable_keeps_service_running_degraded(
    install_client,
) -> None:
    """A Redis that is down at startup disables the cache without raising."""
    install_client(FakeAsyncRedis(ping_errors=99))
    cache = build_cache(retry_delay_seconds=60.0)

    await cache._connection.connect()

    assert await cache.get(CACHE_KEY) is None
    assert await cache.set(CACHE_KEY, b"value") is False
    assert cache._connection.stats()["degraded"] == 1
    assert cache._connection.stats()["connect.error"] == 1


@pytest.mark.asyncio
async def test_get_reconnects_after_startup_outage_without_restart(install_client) -> None:
    """Degradation is temporary: the next read adopts a recovered Redis."""
    client = FakeAsyncRedis(ping_errors=1)
    factory = install_client(client)
    cache = build_cache()
    await cache._connection.connect()
    client.values[CACHE_KEY] = b"recovered"

    value = await cache.get(CACHE_KEY)

    assert value == b"recovered"
    assert factory.call_count == 2
    assert cache._connection.stats()["recovery.transitions"] == 1


@pytest.mark.asyncio
async def test_get_respects_backoff_window_between_reconnect_attempts(install_client) -> None:
    """Inside the backoff window a read falls back without a connect attempt."""
    factory = install_client(FakeAsyncRedis(ping_errors=99))
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    value = await cache.get(CACHE_KEY)

    assert value is None
    assert factory.call_count == 1
    assert cache._connection.stats()["get.unavailable"] == 1


@pytest.mark.asyncio
async def test_reconnect_backoff_uses_jitter_to_spread_recovery_attempts(
    install_client,
    monkeypatch,
) -> None:
    """Reconnect scheduling randomizes attempts within the configured delay window."""
    monkeypatch.setattr(redis_connection_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(redis_connection_module.random, "uniform", lambda low, high: 45.0)
    install_client(FakeAsyncRedis(ping_errors=1))
    cache = build_cache(retry_delay_seconds=60.0)

    await cache._connection.connect()

    assert cache._connection._next_retry_at == 145.0


@pytest.mark.asyncio
async def test_health_check_recovers_after_outage_without_restart(install_client) -> None:
    """Readiness probes bypass the backoff, so probe cadence drives recovery."""
    install_client(FakeAsyncRedis(ping_errors=1))
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    assert await cache._connection.health_check() is True
    assert cache._connection.stats()["connected"] == 1
    assert cache._connection.stats()["degraded"] == 0


@pytest.mark.asyncio
async def test_operations_do_not_reconnect_after_disconnect(install_client) -> None:
    """Shutdown is final: a late request cannot reopen a draining pool."""
    client = FakeAsyncRedis()
    factory = install_client(client)
    cache = build_cache()
    await cache._connection.connect()

    await cache._connection.close()

    assert await cache.get(CACHE_KEY) is None
    assert await cache._connection.health_check() is False
    assert factory.call_count == 1
    assert client.aclose_count == 1


# ── Cache operations ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_stored_value_after_set(install_client) -> None:
    """A stored payload round-trips as raw bytes."""
    install_client(FakeAsyncRedis())
    cache = build_cache()
    await cache._connection.connect()

    assert await cache.set(CACHE_KEY, b"payload", ttl_seconds=60) is True
    assert await cache.get(CACHE_KEY) == b"payload"


@pytest.mark.asyncio
async def test_get_many_preserves_order_and_reports_missing_keys(install_client) -> None:
    """Positional alignment is the contract authorization snapshots rely on."""
    client = FakeAsyncRedis()
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()
    client.values["first"] = b"1"
    client.values["third"] = b"3"

    values = await cache.get_many(("first", "second", "third"))

    assert values == [b"1", None, b"3"]
    assert cache._connection.stats()["get_many.hit"] == 2
    assert cache._connection.stats()["get_many.miss"] == 1


@pytest.mark.asyncio
async def test_get_many_with_no_keys_returns_empty_list(install_client) -> None:
    """An empty read is answered locally rather than sent to Redis."""
    install_client(FakeAsyncRedis())
    cache = build_cache()
    await cache._connection.connect()

    assert await cache.get_many(()) == []


@pytest.mark.asyncio
async def test_conditional_set_is_rejected_after_comparison_value_changes(
    install_client,
) -> None:
    """An invalidation marker changed before the atomic write must win."""
    client = FakeAsyncRedis()
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()
    client.values["version:tenant"] = b"new-version"

    was_stored = await cache.set_if_values_match(
        "grant",
        b"authorized",
        {"version:tenant": None},
        ttl_seconds=60,
    )

    assert was_stored is False
    assert "grant" not in client.values


@pytest.mark.asyncio
async def test_delete_removes_key_and_reports_success(install_client) -> None:
    """Deletion clears the entry and acknowledges the invalidation."""
    client = FakeAsyncRedis()
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()
    client.values[CACHE_KEY] = b"stale"

    assert await cache.delete(CACHE_KEY) is True
    assert await cache.get(CACHE_KEY) is None


@pytest.mark.asyncio
async def test_publish_counts_broadcast_without_subscribers(install_client) -> None:
    """Zero subscribers is success, but is metered apart from a failure."""
    client = FakeAsyncRedis(subscriber_count=0)
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()

    published = await RedisPubSub(cache._connection).publish(CONFIG_CHANGES_CHANNEL, CACHE_KEY)

    assert published is True
    assert client.published_messages == [(CONFIG_CHANGES_CHANNEL, CACHE_KEY)]
    assert cache._connection.stats()["publish.no_subscribers"] == 1


@pytest.mark.asyncio
async def test_get_returns_none_and_counts_error_when_connection_drops(install_client) -> None:
    """A mid-flight connection loss degrades to a miss and is counted."""
    install_client(FakeAsyncRedis(command_error=redis.ConnectionError("dropped")))
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    value = await cache.get(CACHE_KEY)

    assert value is None
    assert cache._connection.stats()["get.error"] == 1
    assert cache._connection.stats()["degraded"] == 1


@pytest.mark.asyncio
async def test_get_propagates_caller_type_errors_instead_of_reporting_a_miss(
    install_client,
) -> None:
    """A caller defect must surface: silently returning None would hide a bug."""
    install_client(FakeAsyncRedis(command_error=TypeError("key must be str")))
    cache = build_cache()
    await cache._connection.connect()

    with pytest.raises(TypeError):
        await cache.get(CACHE_KEY)


# ── Observability ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_separates_hits_misses_and_errors(install_client) -> None:
    """Fallback values look like misses to callers; stats keep them apart."""
    client = FakeAsyncRedis()
    install_client(client)
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()
    client.values[CACHE_KEY] = b"payload"

    await cache.get(CACHE_KEY)
    await cache.get("absent")
    client.command_error = redis.ConnectionError("dropped")
    await cache.get(CACHE_KEY)

    assert cache._connection.stats()["get.hit"] == 1
    assert cache._connection.stats()["get.miss"] == 1
    assert cache._connection.stats()["get.error"] == 1


@pytest.mark.asyncio
async def test_repeated_failures_log_one_degradation_warning(install_client, caplog) -> None:
    """An outage must not flood the log: the transition is logged once."""
    install_client(FakeAsyncRedis(command_error=redis.ConnectionError("dropped")))
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        for _ in range(5):
            await cache.get(CACHE_KEY)

    degradation_warnings = [
        record for record in caplog.records if "Redis connection degraded" in record.getMessage()
    ]
    assert len(degradation_warnings) == 1
    assert cache._connection.stats()["degradation.transitions"] == 1


@pytest.mark.asyncio
async def test_recovery_logs_once_after_degradation(install_client, caplog) -> None:
    """Returning to healthy is announced so operators can close the incident."""
    client = FakeAsyncRedis(command_error=redis.ConnectionError("dropped"))
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()
    await cache.get(CACHE_KEY)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        client.command_error = None
        assert await cache._connection.health_check() is True

    recovery_logs = [
        record for record in caplog.records if "Redis connection recovered" in record.getMessage()
    ]
    assert len(recovery_logs) == 1
    assert cache._connection.stats()["degraded"] == 0


# ── Pub/sub ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_yields_message_payloads_and_skips_control_frames(install_client) -> None:
    """Subscription confirmations share the stream but are not payloads."""
    script = [
        build_subscribe_confirmation_frame(),
        build_message_frame(b"tenant:demo"),
    ]
    install_client(FakeAsyncRedis(pubsub_scripts=[script]))
    cache = build_cache()
    await cache._connection.connect()

    payloads = await collect_payloads(RedisPubSub(cache._connection), expected_count=1)

    assert payloads == ["tenant:demo"]


@pytest.mark.asyncio
async def test_subscribe_resubscribes_after_dropped_connection(install_client) -> None:
    """One network blip must not end distributed invalidation for the process."""
    first_script = [
        build_message_frame(b"before-drop"),
        redis.ConnectionError("subscription dropped"),
    ]
    second_script = [build_message_frame(b"after-reconnect")]
    client = FakeAsyncRedis(pubsub_scripts=[first_script, second_script])
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()

    payloads = await collect_payloads(RedisPubSub(cache._connection), expected_count=2)

    assert payloads == ["before-drop", "after-reconnect"]
    assert len(client.pubsub_handles) == 2


@pytest.mark.asyncio
async def test_disconnect_ends_parked_subscription_without_false_degradation(
    install_client, caplog
) -> None:
    """Shutdown wakes a parked listener quietly, not as an outage warning."""
    client = FakeAsyncRedis(pubsub_scripts=[[]], pubsub_drop_event=asyncio.Event())
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()

    payloads: list[str] = []

    async def consume() -> None:
        async with aclosing(
            RedisPubSub(cache._connection).subscribe(CONFIG_CHANGES_CHANNEL)
        ) as stream:
            async for payload in stream:
                payloads.append(payload)

    task = asyncio.create_task(consume())
    handle = await wait_for_pubsub_handle(client)
    await asyncio.wait_for(handle.awaiting_drop.wait(), timeout=1.0)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await cache._connection.close()

    await asyncio.wait_for(task, timeout=1.0)
    assert payloads == []
    assert handle.aclose_count == 1
    degradation_warnings = [
        record for record in caplog.records if "Redis connection degraded" in record.getMessage()
    ]
    assert degradation_warnings == []


@pytest.mark.asyncio
async def test_subscribe_counts_unavailable_retries_while_redis_is_down(install_client) -> None:
    """Backoff retry cycles during an outage must be visible in stats."""
    install_client(FakeAsyncRedis(ping_errors=99))
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            collect_payloads(RedisPubSub(cache._connection), expected_count=1), timeout=0.2
        )

    assert cache._connection.stats()["subscribe.unavailable"] >= 1


@pytest.mark.asyncio
async def test_subscribe_releases_pubsub_connection_when_consumer_stops(install_client) -> None:
    """Unsubscribing alone leaks the connection, so the handle is closed too."""
    script = [build_message_frame(b"tenant:demo")]
    client = FakeAsyncRedis(pubsub_scripts=[script])
    install_client(client)
    cache = build_cache()
    await cache._connection.connect()

    await collect_payloads(RedisPubSub(cache._connection), expected_count=1)

    handle = client.pubsub_handles[0]
    assert handle.unsubscribed_channels == [CONFIG_CHANGES_CHANNEL]
    assert handle.aclose_count == 1


# ── Synchronous bridge for aiobreaker ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_sync_client_returns_none_while_disconnected(install_client) -> None:
    """The sync bridge degrades like every other method instead of raising."""
    install_client(FakeAsyncRedis(ping_errors=99))
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    assert cache._connection.get_sync_client() is None


@pytest.mark.asyncio
async def test_get_sync_client_builds_client_once_for_repeated_callers(
    install_client, monkeypatch
) -> None:
    """Two callers must share one client rather than leak a second pool."""
    install_client(FakeAsyncRedis())
    created_clients: list[str] = []

    def fake_from_url(url: str, **_kwargs: object) -> object:
        created_clients.append(url)
        return object()

    monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)
    cache = build_cache()
    await cache._connection.connect()

    first = cache._connection.get_sync_client()
    second = cache._connection.get_sync_client()

    assert first is second
    assert created_clients == [REDIS_URL]


@pytest.mark.asyncio
async def test_connection_drop_discards_stale_sync_client(install_client, monkeypatch) -> None:
    """An outage must release the sync pool's sockets and not reuse it later."""
    client = FakeAsyncRedis(command_error=redis.ConnectionError("dropped"))
    install_client(client)
    cache = build_cache(retry_delay_seconds=60.0)
    await cache._connection.connect()

    created_clients: list[FakeSyncRedis] = []

    class FakeSyncRedis:
        """Records pool closes so the test can observe the discard."""

        def __init__(self) -> None:
            self.closed = False
            created_clients.append(self)

        @classmethod
        def from_url(cls, _url: str, **_kwargs: object) -> FakeSyncRedis:
            return cls()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(redis.Redis, "from_url", FakeSyncRedis.from_url)

    first = cache._connection.get_sync_client()
    assert first is not None
    assert not created_clients[0].closed

    await cache.get(CACHE_KEY)

    assert cache._connection.get_sync_client() is None
    assert created_clients[0].closed is True
    assert len(created_clients) == 1

    client.command_error = None
    assert await cache._connection.health_check() is True

    second = cache._connection.get_sync_client()
    assert second is not None and second is not first
    assert len(created_clients) == 2
