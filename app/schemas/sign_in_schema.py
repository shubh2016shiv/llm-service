"""
Sign-In HTTP Contract
=====================

Request and response shapes for ``/api/v1/auth``.

Kept separate from ``auth_schema.py``, which describes identity *inside* the
process (``AuthTokenPayload`` and friends). These models describe what crosses
the wire during sign-in, and the two change for different reasons.

Architecture:
-------------
    ┌────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
    │  dashboard     │────▶│  [These models]      │────▶│  SignInService   │
    │  (browser)     │◀────│  auth router I/O     │◀────│  (services/)     │
    └────────────────┘     └──────────────────────┘     └──────────────────┘

Dependencies:
    - app.schemas.auth_schema — reuses the ``UserRole`` vocabulary so the wire
      contract cannot drift from the roles authorization actually ranks.

Author: Shubham Singh
"""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from app.schemas.auth_schema import UserRole


class SignInRequest(BaseModel):
    """Username and password submitted to exchange for a session token."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=1,
        max_length=255,
        description="Exact username of the account signing in.",
    )
    # SecretStr keeps the password out of tracebacks and repr output. The
    # validation error handler already strips rejected input values, so a
    # malformed body cannot echo it either.
    password: SecretStr = Field(
        min_length=1,
        max_length=1024,
        description="Plaintext password, verified against the stored hash and then discarded.",
    )


class SignedInUser(BaseModel):
    """Who the session belongs to, for display in the dashboard.

    Deliberately minimal. The dashboard fetches full profile data through the
    management API once it holds a token; repeating it here would mean two
    projections of a user that could disagree.
    """

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    username: str
    role: UserRole
    is_guest: bool = Field(
        description="True when this session came from the guest door rather than a password.",
    )


class SessionResponse(BaseModel):
    """A newly issued session: the bearer token and who it speaks for."""

    model_config = ConfigDict(frozen=True)

    access_token: str = Field(description="Bearer token for the Authorization header.")
    token_type: str = Field(default="bearer", description="Always 'bearer'.")
    expires_at: AwareDatetime = Field(
        description=(
            "UTC expiry. Returned explicitly so a client can schedule a "
            "re-authentication prompt without decoding the token."
        ),
    )
    user: SignedInUser


class SignInOptionsResponse(BaseModel):
    """Which sign-in doors this deployment offers.

    The dashboard calls this before rendering the sign-in screen so it can show
    a guest button only where one exists. Unauthenticated by necessity — it is
    what a caller reads to find out how to authenticate — so it carries no
    identity, no user list, and no configuration values beyond these flags.
    """

    model_config = ConfigDict(frozen=True)

    password_sign_in_enabled: bool = Field(
        description="Whether username and password sign-in is available.",
    )
    guest_sign_in_enabled: bool = Field(
        description="Whether an unauthenticated guest superuser session is offered.",
    )
