"""JWT access-token validation for this resource server.

Architecture:
    token_issuer.py (or an external identity service)
            |
            v
    signed bearer token -> validate_access_token -> trusted identity

This module originally documented that the service never issues tokens, on the
premise that a separate identity service would. That premise did not survive
contact with the product: the admin dashboard needs a sign-in, and no identity
service exists in this repository. Issuance now lives in ``token_issuer.py``.

The concern behind the original rule — two authorities quietly inventing
different token contracts — still applies, and is now addressed by keeping both
halves in this package, reading the same settings object, and checking at
startup that the issued lifetime fits inside ``jwt_max_token_age_seconds``.
Externally issued tokens remain valid here provided they carry the same claims.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast, get_args
from uuid import UUID

from jose import JWTError, jwt

from app.core.settings.settings import get_application_settings
from app.schemas.auth_schema import AuthTokenPayload, UserRole

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_REQUIRED_CLAIMS = ("sub", "role", "type", "exp", "iat", "nbf", "iss", "aud", "jti")


def validate_access_token(token: str) -> AuthTokenPayload:
    """Verify one bearer token and return its trusted identity.

    Verification is deliberately complete in one operation: signature,
    algorithm, issuer, audience, time claims, token kind, role, subject, and
    token identifier. Callers cannot decode a token and forget a second check.
    """
    settings = get_application_settings()
    try:
        decoded_claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "require_aud": True,
                "require_exp": True,
                "require_iat": True,
                "require_iss": True,
                "require_jti": True,
                "require_nbf": True,
                "require_sub": True,
                "leeway": settings.jwt_clock_skew_seconds,
            },
        )
    except JWTError as exc:
        logger.warning("JWT validation failed", extra={"error_type": type(exc).__name__})
        raise

    claims: Mapping[str, object] = {
        str(claim_name): claim_value for claim_name, claim_value in decoded_claims.items()
    }
    _require_claims(claims)
    role = _validate_role(claims["role"])
    if claims["type"] != "access":
        raise ValueError("Token is not an access token")

    expires_at = _timestamp(claims["exp"], "exp")
    issued_at = _timestamp(claims["iat"], "iat")
    token_age_seconds = (expires_at - issued_at).total_seconds()
    if not 0 < token_age_seconds <= settings.jwt_max_token_age_seconds:
        raise ValueError(
            "Token lifetime must be positive and no longer than "
            f"{settings.jwt_max_token_age_seconds} seconds"
        )

    return AuthTokenPayload(
        user_id=UUID(str(claims["sub"])),
        role=role,
        token_id=UUID(str(claims["jti"])),
        expires_at=expires_at,
        issued_at=issued_at,
    )


def _require_claims(claims: Mapping[str, object]) -> None:
    """Reject a signed token that omits any part of the service contract."""
    missing_claims = [claim for claim in _REQUIRED_CLAIMS if claim not in claims]
    if missing_claims:
        raise ValueError(f"Token is missing required claims: {missing_claims}")


def _validate_role(raw_role: object) -> UserRole:
    """Convert the role claim only after proving it belongs to the vocabulary."""
    role = str(raw_role)
    if role not in get_args(UserRole):
        raise ValueError(f"Token carries an unknown role: {role!r}")
    return cast("UserRole", role)


def _timestamp(raw_value: object, claim_name: str) -> datetime:
    """Convert a NumericDate claim while producing a useful malformed-token error."""
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"Token claim {claim_name!r} must be a numeric timestamp")
    return datetime.fromtimestamp(raw_value, tz=UTC)
