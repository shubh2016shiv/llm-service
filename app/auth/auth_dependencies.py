"""
Authentication dependencies — the request front door
======================================================

What this file is for
---------------------
FastAPI lets routes declare "before I run, hand me X" via Depends(...).
This file provides the two guards that most protected routes declare:

    1. get_current_user — the identity check. It reads the
       "Authorization: Bearer ..." header, proves the token is genuine
       (correct signature, not expired, right kind), and hands the route
       a typed AuthTokenPayload. Failure = 401 ("who are you?").

    2. RoleGuard — the permission check. It looks at the role inside
       that payload and admits only the roles the route allowed.
       Failure = 403 ("I know who you are, and the answer is no").

Think of a building entrance: the first guard checks that your ID card is
real; the second guard checks your card's clearance level against the
room's door list.

The role ladder (ascending privilege)
-------------------------------------
    developer < operator < admin < owner

Higher rungs include everything below them: a room that requires
"operator" also admits "admin" and "owner". The four pre-built guards at
the bottom of this file (require_developer, require_operator,
require_admin, require_owner) bake that "this rung and above" rule in.

Who uses this file
------------------
    API routes -> get_current_user / RoleGuard -> jwt_token_service

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# logging = writing to the application log.
import logging

# Annotated = attach FastAPI's Depends(...) to a parameter inside a type
# hint, which is how FastAPI knows to build that argument for the route.
from typing import Annotated

# Depends = "FastAPI, please build this argument before the route runs".
# HTTPException/status = the clean 401/403 responses the guards raise.
from fastapi import Depends, HTTPException, status

# OAuth2PasswordBearer = pulls the "Authorization: Bearer ..." header out
# of the request (and documents the login endpoint in the OpenAPI page).
from fastapi.security import OAuth2PasswordBearer

# JWTError = the "this token is not genuine" error family from the JWT
# library (wrong signature, expired, malformed).
from jose import JWTError

# The card printer and verifier (see jwt_token_service.py).
from app.auth.jwt_token_service import decode_token, verify_token_type

# The typed identity every guard hands downstream.
from app.schemas.auth_schema import AuthTokenPayload

# The role vocabulary and the one ordered role ladder. Guards are
# validated against ALL_PLATFORM_ROLES at startup (a typo fails the
# launch), and the pre-built guards below are DERIVED from the ladder
# via platform_roles_at_or_above.
from app.schemas.role_hierarchy import ALL_PLATFORM_ROLES, platform_roles_at_or_above

logger = logging.getLogger(__name__)

# The token extractor. auto_error=False means: when the header is missing,
# do NOT fail here — hand us None so we can raise a friendly, consistent
# 401 ourselves (including the WWW-Authenticate header clients expect).
_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)


async def get_current_user(
    raw_token: Annotated[str | None, Depends(_oauth2_scheme)],
) -> AuthTokenPayload:
    """Prove the caller's identity from the bearer token, or raise 401.

    This is the front-door guard used by nearly every protected route.
    It is "stateless": it checks only what is inside the token (signature,
    expiry, kind) — no database lookup, no session storage.

    Args:
        raw_token: The bearer token from the Authorization header (None
            when the header is absent).

    Returns:
        The decoded, validated identity (AuthTokenPayload).

    Raises:
        HTTPException 401: Missing, invalid, expired, or wrong-kind token.
    """
    # Door check 1: is there a token at all?
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Door check 2: is it genuine, and is it an ACCESS token (not a
    # refresh token, whose only job is obtaining new access tokens)?
    try:
        payload = decode_token(raw_token)
        verify_token_type(payload, "access")
    except JWTError as exc:
        # Wrong signature, expired, or tampered.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except ValueError as exc:
        # Genuine signature but unusable contents (missing claim, bad
        # value). Pydantic's validation errors are ValueError subclasses,
        # so malformed field VALUES land here too — never a 500.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token format is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Identity proven. Log who came in (never the token itself) and hand
    # the typed payload to the route and any further guards.
    logger.debug("Token validated", extra={"user_id": str(payload.user_id), "role": payload.role})
    return payload


class RoleGuard:
    """The door-list guard: admits only the roles a route allowed.

    A guard is a callable object — FastAPI treats it as a dependency just
    like get_current_user, but it runs AFTER the identity check and adds
    the "clearance level" check on top. Higher roles inherit access from
    lower roles: a route requiring "operator" also accepts "admin" and
    "owner".

    Example:
        require_admin = RoleGuard(["admin", "owner"])

        @router.delete("/deployments/{id}")
        async def delete_deployment(
            user: Annotated[AuthTokenPayload, Depends(require_admin)],
        ) -> None:
            ...
    """

    def __init__(self, permitted_roles: list[str]) -> None:
        """Build a guard for one list of allowed roles.

        The list is validated HERE, at construction (startup), so a typo
        like "admni" fails the launch loudly instead of silently locking
        everyone out at request time.

        Args:
            permitted_roles: The role names that may pass this door.

        Raises:
            ValueError: When any name is not a known platform role.
        """
        # Any names that are not on the canonical list of known roles?
        unknown = set(permitted_roles) - ALL_PLATFORM_ROLES
        if unknown:
            raise ValueError(
                f"Unknown roles: {sorted(unknown)}. Permitted values: {sorted(ALL_PLATFORM_ROLES)}"
            )
        # Freeze the allowed set (an unchangeable set — cheap to check,
        # safe to share across every request).
        self._permitted_roles: frozenset[str] = frozenset(permitted_roles)

    def __call__(
        self,
        current_user: Annotated[AuthTokenPayload, Depends(get_current_user)],
    ) -> AuthTokenPayload:
        """Check the caller's role against this door's list.

        Runs after get_current_user (FastAPI resolves nested Depends in
        order), so current_user is already proven genuine here.

        Args:
            current_user: The validated identity from get_current_user.

        Returns:
            The same payload, unchanged, when the role is allowed.

        Raises:
            HTTPException 403: When the role is not on this door's list.
        """
        # Is the caller's role on the list? (The pre-built guards below
        # put every higher rung on each list, so "operator and above" is
        # simply a list containing operator, admin, and owner.)
        if current_user.role not in self._permitted_roles:
            # Log the denial for audit trails — who, with what role, and
            # what was required — then stop the request with 403.
            logger.warning(
                "Role-based access denied",
                extra={
                    "user_id": str(current_user.user_id),
                    "role": current_user.role,
                    "required_roles": sorted(self._permitted_roles),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Insufficient permissions. "
                    f"Required role(s): {', '.join(sorted(self._permitted_roles))}"
                ),
            )

        # Allowed. Log the entry and hand the payload on.
        logger.debug(
            "Role-based access granted",
            extra={"user_id": str(current_user.user_id), "role": current_user.role},
        )
        return current_user


# ---------------------------------------------------------------------------
# Pre-built guards for the four role levels.
# Each guard is DERIVED from the single ordered role ladder in
# app.schemas.role_hierarchy: "this rung and every rung above it" is a
# slice of that ladder, so adding or renaming a role never requires
# hand-editing these lists. sorted() keeps each list stable and tidy.
# ---------------------------------------------------------------------------

require_developer = RoleGuard(sorted(platform_roles_at_or_above("developer")))
"""Admit any authenticated caller with a valid access token."""

require_operator = RoleGuard(sorted(platform_roles_at_or_above("operator")))
"""Admit operators and everything above them."""

require_admin = RoleGuard(sorted(platform_roles_at_or_above("admin")))
"""Admit platform administrators and owners only."""

require_owner = RoleGuard(sorted(platform_roles_at_or_above("owner")))
"""Admit only the highest-privilege owner role."""
