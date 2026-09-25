"""Service-composition dependency for the authentication router.

Routers declare the service they need; this factory wires credential storage,
the failed-attempt limiter, and the guest-door configuration. No business rule
lives here — the module is solely the composition boundary, matching
``management_dependencies`` for the management routers.

It is a separate module from that one because the authentication router is the
only router whose routes are reachable without a token. Keeping its wiring
apart means "everything in management_dependencies serves guarded routes"
stays true.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.adapters.cache import RedisConnectionManager, SignInAttemptLimiter
from app.api.shared_dependencies import PostgresSessionProviderDependency, require_app_state
from app.core.settings.settings import get_application_settings
from app.database import UserPersistence
from app.services.authentication import SignInService


def get_sign_in_attempt_limiter(request: Request) -> SignInAttemptLimiter:
    """Build the failed-attempt limiter over the process Redis connection."""
    settings = get_application_settings()
    connection = require_app_state(
        request,
        "redis_connection",
        RedisConnectionManager,
        hint="Ensure the lifespan created the Redis connection manager.",
    )
    return SignInAttemptLimiter(
        connection,
        max_attempts=settings.sign_in_max_attempts,
        window_seconds=settings.sign_in_attempt_window_seconds,
    )


def get_sign_in_service(
    session_provider: PostgresSessionProviderDependency,
    attempt_limiter: Annotated[SignInAttemptLimiter, Depends(get_sign_in_attempt_limiter)],
) -> SignInService:
    """Build password and guest sign-in over PostgreSQL and Redis."""
    settings = get_application_settings()
    return SignInService(
        user_persistence=UserPersistence(session_provider),
        attempt_limiter=attempt_limiter,
        guest_enabled=settings.guest_superuser_enabled,
        guest_user_id=settings.guest_superuser_user_id,
        guest_role=settings.guest_superuser_role,
    )


SignInServiceDependency = Annotated[SignInService, Depends(get_sign_in_service)]

__all__ = [
    "SignInServiceDependency",
    "get_sign_in_attempt_limiter",
    "get_sign_in_service",
]
