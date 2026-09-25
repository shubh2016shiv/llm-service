"""Sign-in and guest-session configuration.

Architecture:
    environment variables -> AuthSessionConfig -> SignInService / auth router

This module owns the two doors into the dashboard: a password sign-in that any
stored user may use, and an optional guest superuser that skips authentication
entirely. The guest door exists so a developer can open a fresh local stack and
administer it before any password is known.

The guest door is off by default and is refused outright when the process
declares production. That cross-setting rule lives in the settings composition
root, not here, because it needs ``app_environment`` from another concern.
"""

from __future__ import annotations

from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.auth_schema import UserRole


class AuthSessionConfig(BaseModel):
    """Define how this service issues session tokens to dashboard operators.

    Algorithm:
        1. Read the guest toggle, its identity, and the sign-in limiter budget.
        2. Reject a guest configuration that names no user to act as.
        3. Supply the validated values to the sign-in service at startup.
    """

    guest_superuser_enabled: bool = Field(
        default=False,
        description=(
            "Allow an unauthenticated guest session with superuser rights. "
            "Development convenience only; the service refuses to start with "
            "this enabled in production."
        ),
    )
    guest_superuser_user_id: UUID | None = Field(
        default=None,
        description=(
            "Existing users.user_id the guest session acts as. Required when "
            "guest access is enabled: tenant writes verify the caller exists, "
            "so a token for an absent user would fail at the first write."
        ),
    )
    guest_superuser_role: UserRole = Field(
        default="owner",
        description="Platform role granted to the guest session.",
    )
    access_token_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description=(
            "Lifetime of a token this service issues. Must not exceed "
            "jwt_max_token_age_seconds, or this service would mint tokens its "
            "own validator rejects; that pairing is checked in settings.py."
        ),
    )
    sign_in_max_attempts: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Failed password attempts allowed per username inside one window.",
    )
    sign_in_attempt_window_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Window the failed-attempt budget is counted over, and the lockout length.",
    )

    @model_validator(mode="after")
    def validate_guest_identity(self) -> Self:
        """Reject a guest door that is open but has nobody standing behind it."""
        if self.guest_superuser_enabled and self.guest_superuser_user_id is None:
            raise ValueError(
                "guest_superuser_user_id is required when guest_superuser_enabled is true"
            )
        return self
