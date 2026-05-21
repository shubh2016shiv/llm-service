"""
Request Context
===============

Async-safe per-request storage for cross-cutting fields (request ID, etc.)
using Python's built-in ``contextvars`` module.

Why ``ContextVar`` and not ``threading.local``
-----------------------------------------------
FastAPI serves concurrent requests inside a single asyncio event loop — often
in the same OS thread. ``threading.local`` values bleed across concurrent
requests that share a thread. ``ContextVar`` is scoped to the *async task*
(i.e., one per request), so each request gets its own isolated copy that is
set by the middleware and torn down automatically when the task ends.

Usage
-----
In middleware (called once per request)::

    from app.core.request_context import set_request_id
    set_request_id("req-5f3a1b…")

Anywhere downstream in the same request (read-only)::

    from app.core.request_context import get_request_id
    request_id = get_request_id()   # → "req-5f3a1b…"

The sentinel value ``"unset"`` is returned when this is called outside a
request context — for example from a background task spawned before the
middleware has run, or from a unit test that does not configure the context.
"""

from __future__ import annotations

from contextvars import ContextVar

_REQUEST_ID_CONTEXT_VAR: ContextVar[str] = ContextVar("request_id", default="unset")


def set_request_id(request_id: str) -> None:
    """Bind ``request_id`` to the current async task context.

    Called exactly once per request by the ``RequestIdMiddleware``.
    Subsequent reads via ``get_request_id()`` return this value for the
    lifetime of the current async task.

    Args:
        request_id: The UUID string assigned to this request.
    """
    _REQUEST_ID_CONTEXT_VAR.set(request_id)


def get_request_id() -> str:
    """Return the request ID bound to the current async task context.

    Returns:
        The UUID string set by the middleware, or ``"unset"`` when called
        outside a request context (background tasks, CLI scripts, tests).
    """
    return _REQUEST_ID_CONTEXT_VAR.get()
