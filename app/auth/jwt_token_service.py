"""
app/auth/jwt_token_service.py — JWT token creation, decoding, and validation.

All operations are cryptographic — no database I/O is performed here.
The secret key and algorithm come from ApplicationSettings so they are
never hardcoded.

Security notes:
- HS256 is the default algorithm; swap for RS256 in multi-service environments
  where the signing and verification parties differ.
- Including ``token_type`` in every payload prevents refresh tokens from being
  used to authorise API calls (token confusion attack).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from app.core.settings.settings import get_application_settings
from app.schemas.auth_schema import AuthTokenPayload

logger = logging.getLogger(__name__)

# Roles accepted by this service, listed from lowest to highest privilege.
_VALID_ROLES: frozenset[str] = frozenset({"developer", "operator", "admin", "owner"})


def _build_token_claims(
    user_id: UUID,
    role: str,
    token_type: str,
    expires_at: datetime,
) -> dict[str, object]:
    """Assemble the standard JWT claim set for this service."""
    now = datetime.now(UTC)
    return {
        "user_id": str(user_id),
        "role": role,
        "type": token_type,
        "exp": expires_at,
        "iat": now,
    }


def _assert_valid_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise ValueError(f"Role {role!r} is not valid. Must be one of: {sorted(_VALID_ROLES)}")


def create_access_token(user_id: UUID, role: str) -> str:
    """Create a signed JWT access token.

    Args:
        user_id: UUID of the authenticated user.
        role: User's role — developer, operator, admin, or owner.

    Returns:
        Signed JWT string ready to be returned to the client.

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

    Refresh tokens have a longer lifetime than access tokens and may only
    be exchanged for a new access token — they cannot authorise API calls.

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

    jose validates the signature and expiry automatically. This function
    additionally maps the raw claim dict to a typed ``AuthTokenPayload``.

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

    Prevents refresh tokens from being accepted where access tokens are required,
    guarding against token confusion attacks.

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
