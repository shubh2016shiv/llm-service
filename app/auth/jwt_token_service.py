"""
JWT Token Service
=================

Creates, decodes, and validates JWTs used for authenticated API sessions.

This module is intentionally stateless: it performs cryptographic token
operations and claim validation without querying database state.

Enterprise Pattern: Stateless Authentication Pattern
    Token validation is self-contained and deterministic, which helps services
    scale horizontally without a shared session store.

Security note:
    Every token includes a ``type`` claim so refresh tokens cannot be used where
    access tokens are required.

Step-by-step relation in auth flow:
    1. Login flow calls ``create_access_token`` (and optionally refresh token).
    2. Client sends access token on API requests.
    3. Route dependency calls ``decode_token`` to verify signature and claims.
    4. ``verify_token_type`` enforces context correctness (access vs refresh).

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, get_args
from uuid import UUID

from jose import JWTError, jwt

from app.core.settings.settings import get_application_settings
from app.schemas.auth_schema import AuthTokenPayload, UserRole

logger = logging.getLogger(__name__)

# Derived from the canonical UserRole Literal so adding a role in auth_schema.py
# is the single change required — no need to update this set manually.
_VALID_ROLES: frozenset[str] = frozenset(get_args(UserRole))


def _build_token_claims(
    user_id: UUID,
    role: str,
    token_type: str,
    expires_at: datetime,
) -> dict[str, object]:
    """Assemble standard claims required by this service's token contract.

    Centralizing claim construction keeps access and refresh token payloads
    consistent, reducing drift between token types.
    """
    now = datetime.now(UTC)
    return {
        "user_id": str(user_id),
        "role": role,
        "type": token_type,
        "exp": expires_at,
        "iat": now,
    }


def _assert_valid_role(role: str) -> None:
    """Validate role against the canonical role set before token issuance."""
    if role not in _VALID_ROLES:
        raise ValueError(f"Role {role!r} is not valid. Must be one of: {sorted(_VALID_ROLES)}")


def create_access_token(user_id: UUID, role: str) -> str:
    """Create a signed JWT access token.

    Args:
        user_id: UUID of the authenticated user.
        role: User's role — developer, operator, admin, or owner.

    Returns:
        Signed JWT string intended for API request authorization.

    Raises:
        ValueError: If the role is not in the allowed set.
        JWTError: If token signing fails.
    """
    _assert_valid_role(role)
    settings = get_application_settings()
    expires_at = datetime.now(UTC) + timedelta(hours=settings.jwt_access_token_expire_hours)
    claims = _build_token_claims(user_id, role, "access", expires_at)

    token: str = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    logger.debug("Access token created | user_id=%s role=%s", user_id, role)
    return token


def create_refresh_token(user_id: UUID, role: str) -> str:
    """Create a signed JWT refresh token.

    Refresh tokens have a longer lifetime than access tokens and are meant
    only for token rotation flows, not direct API authorization.

    Args:
        user_id: UUID of the authenticated user.
        role: User's role.

    Returns:
        Signed JWT refresh token string.

    Raises:
        ValueError: If refresh tokens are disabled or the role is invalid.
        JWTError: If token signing fails.
    """
    settings = get_application_settings()
    if not settings.jwt_refresh_enabled:
        raise ValueError("Refresh tokens are disabled. Set JWT_REFRESH_ENABLED=true to enable.")

    _assert_valid_role(role)
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    claims = _build_token_claims(user_id, role, "refresh", expires_at)

    token: str = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    logger.debug("Refresh token created | user_id=%s role=%s", user_id, role)
    return token


def decode_token(token: str) -> AuthTokenPayload:
    """Decode and cryptographically validate a JWT token.

    ``python-jose`` validates signature and expiry. This function then
    validates required custom claims and maps raw values into the strongly
    typed ``AuthTokenPayload`` used across the application.

    Args:
        token: Raw JWT string from the Authorization header.

    Returns:
        Decoded and validated ``AuthTokenPayload``.

    Raises:
        JWTError: If the token is expired, tampered, or malformed.
        ValueError: If a required claim is missing from the payload.
    """
    settings = get_application_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        logger.warning("JWT decode failed: %s", exc)
        raise

    # Validate presence of all required custom claims.
    for required_claim in ("user_id", "role", "type", "exp", "iat"):
        if required_claim not in claims:
            raise ValueError(f"Token is missing required claim: {required_claim!r}")

    return AuthTokenPayload(
        user_id=UUID(str(claims["user_id"])),
        role=str(claims["role"]),  # type: ignore[arg-type]
        token_type=str(claims["type"]),  # type: ignore[arg-type]
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
    )


def verify_token_type(payload: AuthTokenPayload, expected_token_type: str) -> None:
    """Assert that a decoded token is of the expected type.

    Prevents token confusion, where a token valid in one context (refresh)
    is mistakenly accepted in another context (access).

    Args:
        payload: Decoded token payload from ``decode_token``.
        expected_token_type: Either ``"access"`` or ``"refresh"``.

    Raises:
        ValueError: If the token type does not match.
    """
    if payload.token_type != expected_token_type:
        raise ValueError(
            f"Token type mismatch: expected {expected_token_type!r}, got {payload.token_type!r}"
        )
