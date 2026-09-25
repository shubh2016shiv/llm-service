"""ASGI application factory and process-scoped composition root.

Architecture:
    uvicorn --factory app.main:create_app
        -> validated settings -> FastAPI routes/middleware
        -> lifespan -> bootstrap.configure_runtime -> owned adapter pools

Importing this module is intentionally side-effect free: settings are loaded
when the server invokes the factory, not when tests import the module.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth_router, llm_inference_router, management_router
from app.api.exception_handlers import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.request_context import (
    RequestBodyLimitMiddleware,
    RequestContextMiddleware,
    UnhandledExceptionMiddleware,
)
from app.bootstrap import configure_runtime
from app.core.logging import configure_logging
from app.core.settings.settings import get_application_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from app.core.settings.settings import ApplicationSettings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start and close owned resources even when startup or shutdown fails."""
    settings: ApplicationSettings = app.state.settings
    configure_logging(
        level=settings.log_level,
        format="text" if settings.app_environment == "development" else "json",
        environment=settings.app_environment,
    )
    async with AsyncExitStack() as stack:
        await configure_runtime(app, settings, stack)
        logger.info("Application startup complete", extra={"environment": settings.app_environment})
        try:
            yield
        finally:
            logger.info("Application shutdown started")
    logger.info("Application shutdown complete")


def create_app(settings: ApplicationSettings | None = None) -> FastAPI:
    """Build the API from validated settings; pass settings explicitly in tests.

    Example:
        create_app(ApplicationSettings(...))

    Uvicorn calls this factory once per worker with no argument. Keeping the
    settings load here prevents invalid environment variables from breaking
    ordinary module imports or test discovery.
    """
    resolved = settings if settings is not None else get_application_settings()
    app = _build_fastapi(resolved)
    app.state.settings = resolved
    _register_middleware(app, resolved)
    app.include_router(auth_router)
    app.include_router(llm_inference_router)
    app.include_router(management_router)
    app.include_router(health_router)
    register_exception_handlers(app)
    return app


def _build_fastapi(settings: ApplicationSettings) -> FastAPI:
    """Turn deployment policy into versioned API and docs exposure."""
    production = settings.app_environment == "production"
    return FastAPI(
        title="LLM Provider Service",
        description="Multi-tenant LLM inference through authorized deployments.",
        version=settings.service_version,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        lifespan=lifespan,
    )


def _register_middleware(app: FastAPI, settings: ApplicationSettings) -> None:
    """Register innermost first so errors still receive CORS and request IDs."""
    app.add_middleware(
        RequestBodyLimitMiddleware, max_body_bytes=settings.api_max_request_body_bytes
    )
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Deployment-Key",
            "X-Request-ID",
            "X-Tenant-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
