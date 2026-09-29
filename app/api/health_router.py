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

from app.adapters.cache import RedisConnectionManager
from app.adapters.postgresql import PostgresSessionProvider
from app.adapters.provider_transport import ProviderCircuitBreakerRegistry
from app.core.settings.settings import ApplicationSettings
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
    settings = getattr(request.app.state, "settings", None)
    settings_healthy = isinstance(settings, ApplicationSettings)
    statuses = {"postgresql": database_healthy, "redis": redis_healthy}
    ready = settings_healthy and all(statuses.values())
    if not ready:
        failing_dependencies = [name for name, healthy in statuses.items() if not healthy]
        if not settings_healthy:
            failing_dependencies.append("settings")
        logger.warning(
            "Readiness check failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "failing_dependencies": failing_dependencies,
            },
        )
    content: dict[str, object] = {"status": "ready" if ready else "degraded"}
    # If settings are absent or malformed, fail closed without disclosing
    # dependency details, matching production response behavior.
    if isinstance(settings, ApplicationSettings) and settings.app_environment != "production":
        content["dependencies"] = {
            name: "ok" if healthy else "unavailable" for name, healthy in statuses.items()
        }
    return JSONResponse(status_code=200 if ready else 503, content=content)


@router.get("/health/diagnostics")
async def operational_diagnostics(request: Request) -> dict[str, object]:
    """Return non-secret, process-local counters for operator diagnosis.

    This endpoint is deliberately separate from readiness. A worker at stream
    capacity is healthy and should remain behind the load balancer; callers can
    retry another worker using the 503/Retry-After admission response.
    """
    state = request.app.state
    # Starlette's state container is intentionally dynamic. Narrow from
    # ``object`` below so both readers and the type checker can see that every
    # adapter method is called only after its concrete runtime type is proven.
    limiter: object = getattr(state, "stream_capacity_limiter", None)
    breakers: object = getattr(state, "provider_circuit_breaker_registry", None)
    postgres: object = getattr(state, "postgres_session_provider", None)
    redis: object = getattr(state, "redis_connection", None)

    streaming: dict[str, object] = {"available": False}
    if isinstance(limiter, WorkerStreamCapacityLimiter):
        streaming = {
            "available": True,
            "active_streams": limiter.active_stream_count,
            "max_concurrent_streams": limiter.max_concurrent_streams,
        }

    # Only aggregate breaker states. Provider names are useful internally but
    # revealing the configured provider inventory is unnecessary for deciding
    # whether this worker has open circuits.
    circuit_counts = {"closed": 0, "open": 0, "half_open": 0}
    circuits_available = False
    if isinstance(breakers, ProviderCircuitBreakerRegistry):
        circuits_available = True
        for breaker_state in breakers.snapshot_states().values():
            normalized_state = breaker_state.lower()
            if normalized_state in circuit_counts:
                circuit_counts[normalized_state] += 1

    return {
        "scope": "process-local",
        "streaming": streaming,
        "provider_circuits": {
            "available": circuits_available,
            "states": circuit_counts,
        },
        "postgresql": {
            "available": isinstance(postgres, PostgresSessionProvider),
            "counters": postgres.stats() if isinstance(postgres, PostgresSessionProvider) else {},
        },
        "redis": {
            "available": isinstance(redis, RedisConnectionManager),
            "counters": redis.stats() if isinstance(redis, RedisConnectionManager) else {},
        },
    }


async def _probe(name: str, check: Callable[[], Awaitable[bool]] | None) -> bool:
    """Treat an unexpected adapter failure as unready, never as an HTTP 500."""
    if check is None:
        return False
    try:
        return await check()
    except Exception:
        logger.exception("Readiness dependency probe raised", extra={"dependency": name})
        return False
