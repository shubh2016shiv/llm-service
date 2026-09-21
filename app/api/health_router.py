"""Minimal public liveness and bounded, non-disclosing readiness probes.

Architecture:
    load balancer -> health_router -> Postgres / Redis adapter health checks

Redis is required here: authorization grants and their invalidation versions
share its store. The resolver may fall back to PostgreSQL for route data, but
that does not make an unavailable authorization cache safe to ignore.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.adapters.cache import RedisConnectionManager
    from app.adapters.postgresql import PostgresSessionProvider

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


@router.get("/health")
async def liveness_check() -> dict[str, str]:
    """Report that the HTTP process is running without querying dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness_check(request: Request) -> JSONResponse:
    """Reject traffic if a required source of truth or invalidation store fails.

    These checks are bounded by the adapters' own network timeouts. Vault and
    the token manager have no safe side-effect-free probe contract here; their
    per-request failures are handled by the inference error boundary instead.
    """
    postgres: PostgresSessionProvider | None = getattr(
        request.app.state, "postgres_session_provider", None
    )
    redis: RedisConnectionManager | None = getattr(request.app.state, "redis_connection", None)
    database_healthy, redis_healthy = await asyncio.gather(
        _probe("postgresql", postgres.health_check if postgres else None),
        _probe("redis", redis.health_check if redis else None),
    )
    statuses = {"postgresql": database_healthy, "redis": redis_healthy}
    ready = all(statuses.values())
    if not ready:
        logger.warning(
            "Readiness check failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "failing_dependencies": [name for name, healthy in statuses.items() if not healthy],
            },
        )
    settings = request.app.state.settings
    content: dict[str, object] = {"status": "ready" if ready else "degraded"}
    if settings.app_environment != "production":
        content["dependencies"] = {
            name: "ok" if healthy else "unavailable" for name, healthy in statuses.items()
        }
    return JSONResponse(status_code=200 if ready else 503, content=content)


async def _probe(name: str, check: Callable[[], Awaitable[bool]] | None) -> bool:
    """Treat an unexpected adapter failure as unready, never as an HTTP 500."""
    if check is None:
        return False
    try:
        return await check()
    except Exception:
        logger.exception("Readiness dependency probe raised", extra={"dependency": name})
        return False
