"""Shared API dependencies: application state and authorization building blocks.

Process-owned resources are created by FastAPI's lifespan and placed on
``app.state``. This module is the typed door through which request code reaches
them. A missing or wrong-typed value fails immediately with startup guidance
instead of surfacing later as an unrelated attribute error.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.adapters.postgresql import PostgresSessionProvider
from app.auth.authorization import AuthorizationGrantCache, TenantAccessService
from app.core.settings.loader import ConfigLoader
from app.core.settings.settings import get_application_settings
from app.database import TenantMembershipPersistence
from app.inference_routing.route_resolution import InferenceRouteResolver
from app.services.credential_encoding import CredentialWriter


def require_app_state[AppStateValue](
    request: Request,
    attr_name: str,
    expected_type: type[AppStateValue],
    *,
    hint: str,
) -> AppStateValue:
    """Return one lifespan-owned resource after presence and type checks."""
    value = getattr(request.app.state, attr_name, None)
    if value is None:
        raise RuntimeError(f"app.state.{attr_name} is not initialized. {hint}")
    if not isinstance(value, expected_type):
        raise RuntimeError(
            f"app.state.{attr_name} has type {type(value).__name__}; "
            f"expected {expected_type.__name__}. {hint}"
        )
    return value


def get_postgres_session_provider(request: Request) -> PostgresSessionProvider:
    """Return the application-scoped PostgreSQL pool/session provider."""
    return require_app_state(
        request,
        "postgres_session_provider",
        PostgresSessionProvider,
        hint="Ensure the lifespan created the PostgreSQL adapter.",
    )


PostgresSessionProviderDependency = Annotated[
    PostgresSessionProvider,
    Depends(get_postgres_session_provider),
]


def get_credential_writer(request: Request) -> CredentialWriter | None:
    """Return the optional write-only credential backend."""
    return getattr(request.app.state, "credential_writer", None)


CredentialWriterDependency = Annotated[
    CredentialWriter | None,
    Depends(get_credential_writer),
]


def get_inference_authorization_cache(request: Request) -> AuthorizationGrantCache:
    """Build a request-light cache façade around the process Redis adapter."""
    settings = get_application_settings()
    return AuthorizationGrantCache(
        backend=getattr(request.app.state, "redis_cache", None),
        ttl_seconds=settings.inference_authorization_cache_ttl_seconds,
    )


AuthorizationCacheDependency = Annotated[
    AuthorizationGrantCache,
    Depends(get_inference_authorization_cache),
]


def get_tenant_access_service(
    session_provider: PostgresSessionProviderDependency,
) -> TenantAccessService:
    """Build tenant-scope authorization against membership persistence."""
    return TenantAccessService(TenantMembershipPersistence(session_provider))


TenantAccessServiceDependency = Annotated[
    TenantAccessService,
    Depends(get_tenant_access_service),
]


def get_inference_route_resolver(request: Request) -> InferenceRouteResolver:
    """Return the process-scoped route resolver built during startup."""
    return require_app_state(
        request,
        "inference_route_resolver",
        InferenceRouteResolver,
        hint="Ensure the lifespan created the inference route resolver.",
    )


def get_config_loader(request: Request) -> ConfigLoader:
    """Return the process-scoped provider/model configuration loader."""
    return require_app_state(
        request,
        "config_loader",
        ConfigLoader,
        hint="Ensure the lifespan created the configuration loader.",
    )
