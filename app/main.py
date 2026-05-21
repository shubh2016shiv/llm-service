"""
Application entry point.

Startup sequence (lifespan handler):
    1. Load ApplicationSettings from environment / .env
    2. Load GlobalConfig from YAML (config_dir)
    3. Connect RedisCache
    4. Build HTTPClientFactory  (shared TCP/TLS connection pool)
    5. Build ConfigLoader       (YAML-backed provider/model metadata)
    6. Build ProviderRegistry   (singleton cache of provider instances)
    7. Build DeploymentResolver (reads tenant deployment config from Redis)
    8. Build TokenManagerClient (quota check / usage reporting)
    9. Build InferenceService   (orchestrates steps 7-8 per request)
   10. Store InferenceService on app.state (survives across requests)

Shutdown sequence (lifespan handler, reversed):
    - Close RedisCache connection pool
    - (HTTPClientFactory transport is GC'd; httpx handles cleanup)
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import llm_inference_router, management_router
from app.clients.token_manager_client import TokenManagerClient
from app.core.exceptions import (
    LLMServiceError,
    ManagementError,
    ManagementValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
    TenantAccessDeniedError,
)
from app.core.logging import configure_logging
from app.core.request_context import get_request_id, set_request_id
from app.core.secret_store import EnvironmentSecretStore, SecretStore, VaultSecretStore
from app.core.settings.loader import ConfigLoader
from app.core.settings.settings import get_application_settings
from app.execution.inference_service import InferenceService
from app.infrastructure.http_client_factory import HTTPClientFactory
from app.infrastructure.redis_cache import RedisCache
from app.providers.registry import ProviderRegistry
from app.routing.deployment_resolver import DeploymentResolver

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Wire all application-scoped dependencies and store them on app.state."""
    settings = get_application_settings()

    # Configure structured logging first so every subsequent startup log is
    # captured in the chosen format. JSON in non-development environments;
    # human-readable text locally.
    log_format = "text" if settings.app_environment == "development" else "json"
    configure_logging(
        level=settings.log_level,
        format=log_format,
        environment=settings.app_environment,
    )

    config_loader = ConfigLoader(
        config_dir=Path(settings.config_dir),
        environment=settings.app_environment,
    )
    global_config = config_loader.load_global_config()

    redis_cache = RedisCache(redis_url=settings.redis_url)
    await redis_cache.connect()
    app.state.redis_cache = redis_cache

    http_client_factory = HTTPClientFactory(pool_config=global_config.http_pool)

    secret_store: SecretStore
    if settings.secret_backend == "vault":
        if settings.vault_username is None or settings.vault_password is None:
            raise RuntimeError(
                "secret_backend=vault requires VAULT_USERNAME and VAULT_PASSWORD to be set."
            )
        secret_store = VaultSecretStore(
            vault_addr=settings.vault_addr,
            username=settings.vault_username,
            password=settings.vault_password.get_secret_value(),
            mount_path=settings.vault_mount_path,
            kv_prefix=settings.vault_kv_prefix,
        )
        logger.info("Secret backend: Vault at %s", settings.vault_addr)
    else:
        secret_store = EnvironmentSecretStore()
        logger.info("Secret backend: environment variables")

    provider_registry = ProviderRegistry(
        http_client_factory=http_client_factory,
        config_loader=config_loader,
        cache=redis_cache,
        secret_store=secret_store,
    )

    deployment_resolver = DeploymentResolver(cache=redis_cache)
    token_manager_client = TokenManagerClient()

    app.state.inference_service = InferenceService(
        deployment_resolver=deployment_resolver,
        token_manager_client=token_manager_client,
        provider_registry=provider_registry,
    )

    logger.info("Application startup complete | environment=%s", settings.app_environment)

    yield

    await secret_store.aclose()
    await redis_cache.disconnect()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="LLM Provider Service",
    description=(
        "Multi-tenant LLM provider abstraction layer. "
        "Routes inference requests to the correct provider based on tenant deployment config."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(llm_inference_router)
app.include_router(management_router)


# ---------------------------------------------------------------------------
# Request ID Middleware
#
# Assigns a unique correlation ID to every inbound request. If the upstream
# caller (API gateway, load balancer, or service mesh) already set an
# X-Request-ID header, we honour it so the ID is consistent across the full
# distributed call chain. Otherwise we generate a new UUID4.
#
# The ID is stored in a ContextVar (app.core.request_context) so it is
# accessible anywhere in the async call stack without being threaded through
# every function signature.
# ---------------------------------------------------------------------------


@app.middleware("http")
async def attach_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Inject a per-request correlation ID and propagate it through the response.

    Resolution order for the request ID:
      1. X-Request-ID header provided by the caller / upstream proxy.
      2. Freshly generated UUID4 if no header is present.

    The selected ID is:
      - Stored in ``_REQUEST_ID_CONTEXT_VAR`` for the duration of this request.
      - Echoed back in the ``X-Request-ID`` response header so callers can
        correlate their own logs with server-side log entries.
    """
    incoming_request_id = request.headers.get("X-Request-ID")
    request_id = incoming_request_id if incoming_request_id else str(uuid.uuid4())

    set_request_id(request_id)

    logger.debug(
        "Request received | request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError) -> JSONResponse:
    """Safety-net handler for any LLMServiceError that escaped route-level handlers.

    Route handlers and dependencies should translate domain exceptions themselves.
    This handler catches anything that slips through — most commonly from list
    endpoints that lack try/except — and guarantees a structured JSON response
    instead of a raw 500.
    """
    if isinstance(exc, ResourceNotFoundError):
        status_code = 404
    elif isinstance(exc, ResourceConflictError):
        status_code = 409
    elif isinstance(exc, TenantAccessDeniedError):
        status_code = 403
    elif isinstance(exc, (ManagementValidationError, ManagementError)):
        status_code = 400
    else:
        status_code = 500

    if status_code == 500:
        logger.error(
            "Unhandled domain exception | request_id=%s method=%s path=%s exc_type=%s",
            get_request_id(),
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=True,
        )
    else:
        logger.warning(
            "Domain exception caught by global handler | request_id=%s method=%s path=%s exc_type=%s",
            get_request_id(),
            request.method,
            request.url.path,
            type(exc).__name__,
        )

    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.get("/health", tags=["Health"])
async def liveness_check() -> dict[str, str]:
    """Liveness check — confirms the process is alive and the event loop is running.

    Kubernetes / container orchestrators call this to decide whether to restart
    the container. It performs no I/O; it always returns 200 as long as the
    process is not deadlocked.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dependency status labels — used in both the response body and log output.
# ---------------------------------------------------------------------------
_DEPENDENCY_STATUS_HEALTHY = "ok"
_DEPENDENCY_STATUS_UNAVAILABLE = "unavailable"


@app.get("/health/ready", tags=["Health"])
async def readiness_check(request: Request) -> JSONResponse:
    """Readiness check — verifies every runtime dependency is reachable.

    Returns HTTP 200 when all dependencies are healthy and the service is
    ready to accept traffic. Returns HTTP 503 when one or more dependencies
    are unavailable; the response body names the failing dependency so
    operators can pinpoint the problem without reading application logs.

    Distinct from ``/health`` (liveness):
        - Liveness failure → orchestrator restarts the container.
        - Readiness failure → orchestrator removes the instance from the load
          balancer pool without restarting it, giving the dependency time to
          recover while the process stays alive.

    Dependency checks:
        redis — required for deployment config caching, authorization caching,
                and config-change pub/sub. Inference requests will still be
                served from the database on a cache miss, but latency increases.
    """
    redis_cache: RedisCache | None = getattr(request.app.state, "redis_cache", None)

    redis_healthy = (
        await redis_cache.health_check()
        if redis_cache is not None
        else False
    )

    dependency_health: dict[str, str] = {
        "redis": _DEPENDENCY_STATUS_HEALTHY if redis_healthy else _DEPENDENCY_STATUS_UNAVAILABLE,
    }

    all_dependencies_healthy = all(
        status_label == _DEPENDENCY_STATUS_HEALTHY
        for status_label in dependency_health.values()
    )

    if not all_dependencies_healthy:
        failing = [name for name, label in dependency_health.items() if label != _DEPENDENCY_STATUS_HEALTHY]
        logger.warning(
            "Readiness check failed | request_id=%s failing_dependencies=%s",
            get_request_id(),
            failing,
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "dependencies": dependency_health,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "dependencies": dependency_health,
        },
    )
