"""
Authentication Dependencies
===========================

FastAPI dependency utilities for route protection.

This module provides the two guard layers that most authenticated routes need:
    1. Token validation - verify signature, expiry, and token type.
    2. Role gating - enforce endpoint-specific role requirements.

Enterprise Pattern: Dependency Guard Pattern
    Route handlers declare access requirements with ``Depends(...)`` and receive
    a validated user payload only when checks pass.

Step-by-step relation in request flow:
    1. ``OAuth2PasswordBearer`` extracts the ``Authorization: Bearer ...`` token.
    2. ``get_current_user`` decodes JWT and validates ``type="access"``.
    3. A ``RoleGuard`` instance checks allowed roles for the endpoint.
    4. Route logic runs only if both checks pass.

Role hierarchy (ascending privilege):
    developer < operator < admin < owner

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import Annotated, get_args

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from app.auth.jwt_token_service import decode_token, verify_token_type
from app.schemas.auth_schema import AuthTokenPayload, UserRole

logger = logging.getLogger(__name__)

_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)

# Derived from the canonical UserRole Literal — single source of truth.
_VALID_ROLES: frozenset[str] = frozenset(get_args(UserRole))


async def get_current_user(
    raw_token: Annotated[str | None, Depends(_oauth2_scheme)],
) -> AuthTokenPayload:
    """Extract and validate the JWT from the Authorization header.

    This is the base dependency for authenticated endpoints. It performs
    stateless cryptographic checks only; no database lookup is required.

    "Stateless" here means validation relies entirely on token contents and
    signing secret, not on server-side session storage.

    Args:
        raw_token: Bearer token extracted by OAuth2PasswordBearer.

    Returns:
        Decoded and validated ``AuthTokenPayload``.

    Raises:
        HTTPException 401: If the token is absent, expired, or invalid.
    """
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(raw_token)
        verify_token_type(payload, "access")
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token format is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    logger.debug(
        "Token validated | user_id=%s role=%s",
        payload.user_id,
        payload.role,
    )
    return payload


class RoleGuard:
    """FastAPI callable dependency that enforces a minimum role level.

    Higher roles inherit access from lower roles. A route requiring
    ``operator`` therefore accepts ``operator``, ``admin``, and ``owner``.

    Example:
        require_admin = RoleGuard(["admin", "owner"])

        @router.delete("/deployments/{id}")
        async def delete_deployment(
            user: Annotated[AuthTokenPayload, Depends(require_admin)],
        ) -> None:
            ...
    """

    def __init__(self, permitted_roles: list[str]) -> None:
        """Initialise the guard with the roles that may access the endpoint.

        Args:
            permitted_roles: Roles that are granted access. Validated immediately
                             so misconfiguration fails at startup, not at request time.

        Raises:
            ValueError: If any role in ``permitted_roles`` is not a known role.
        """
        unknown = set(permitted_roles) - _VALID_ROLES
        if unknown:
            raise ValueError(
                f"Unknown roles: {sorted(unknown)}. Permitted values: {sorted(_VALID_ROLES)}"
            )
        self._permitted_roles: frozenset[str] = frozenset(permitted_roles)

    def __call__(
        self,
        current_user: Annotated[AuthTokenPayload, Depends(get_current_user)],
    ) -> AuthTokenPayload:
        """Enforce the role requirement for the current request.

        Args:
            current_user: Validated token payload from ``get_current_user``.

        Returns:
            The same ``AuthTokenPayload`` if the role check passes.

        Raises:
            HTTPException 403: If role requirements are not satisfied.
        """
        if current_user.role not in self._permitted_roles:
            logger.warning(
                "Access denied | user_id=%s role=%s required_roles=%s",
                current_user.user_id,
                current_user.role,
                sorted(self._permitted_roles),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Insufficient permissions. "
                    f"Required role(s): {', '.join(sorted(self._permitted_roles))}"
                ),
            )

        logger.debug(
            "Access granted | user_id=%s role=%s",
            current_user.user_id,
            current_user.role,
        )
        return current_user


# ---------------------------------------------------------------------------
# Pre-built guards for the four role levels.
# Import and use these directly in route Depends() calls.
# ---------------------------------------------------------------------------

require_developer = RoleGuard(["developer", "operator", "admin", "owner"])
"""Grant access to any authenticated caller with a valid access token."""

require_operator = RoleGuard(["operator", "admin", "owner"])
"""Grant access to operational and higher-privilege roles."""

require_admin = RoleGuard(["admin", "owner"])
"""Grant access to administrative platform roles only."""

require_owner = RoleGuard(["owner"])
"""Grant access exclusively to the highest-privilege owner role."""
