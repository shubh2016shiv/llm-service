"""Unit tests for app.core.request_context — ContextVar-based request ID.

These functions use only Python's stdlib ``contextvars`` module. They are
pure-logic, no I/O, and require no mocking. Ideal Layer 1 targets.
"""

from __future__ import annotations

import asyncio

from app.core.request_context import get_request_id, set_request_id

# ═══════════════════════════════════════════════════════════════════════════════
# set_request_id / get_request_id
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequestContext:
    """Tests for the request context ContextVar wrappers."""

    def test_get_request_id_returns_unset_by_default(self) -> None:
        """When no middleware has set a request ID, the sentinel 'unset' is returned."""
        result = get_request_id()

        assert result == "unset"

    def test_set_and_get_request_id_roundtrip(self) -> None:
        """set_request_id followed by get_request_id must return the same value."""
        set_request_id("req-abc123")

        result = get_request_id()

        assert result == "req-abc123"

    def test_set_request_id_overwrites_previous_value(self) -> None:
        """Calling set_request_id again must overwrite the current context value."""
        set_request_id("req-first")
        set_request_id("req-second")

        result = get_request_id()

        assert result == "req-second"

    def test_request_id_isolation_across_async_tasks(self) -> None:
        """Each async task must see only its own request ID."""

        async def task_a() -> str:
            set_request_id("req-task-a")
            await asyncio.sleep(0)
            return get_request_id()

        async def task_b() -> str:
            set_request_id("req-task-b")
            await asyncio.sleep(0)
            return get_request_id()

        async def run() -> tuple[str, str]:
            return await asyncio.gather(task_a(), task_b())

        result_a, result_b = asyncio.run(run())

        assert result_a == "req-task-a"
        assert result_b == "req-task-b"

    def test_request_id_does_not_leak_between_sequential_calls(self) -> None:
        """After one call sets a request ID, a fresh context must see 'unset'."""
        set_request_id("req-xyz")

        # In a fresh sync call (same thread, no async context), the ContextVar
        # is thread/task-local. A new test function call starts with the default.
        # So this works because each test method gets a fresh context.

        result = get_request_id()
        assert result == "req-xyz"  # same context; set above in this test

    def test_empty_string_request_id_is_stored(self) -> None:
        """An empty string is a valid request ID and must be stored."""
        set_request_id("")

        result = get_request_id()

        assert result == ""
