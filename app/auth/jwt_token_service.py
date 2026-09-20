"""
JWT token service — the ID card printer and verifier
=====================================================

What this file is for
---------------------
Sessions here work with ID cards instead of server-side memory. A JWT
(JSON Web Token) is a small signed card:

    - anyone can READ it (it is just encoded text),
    - only this service can MAKE a valid one (it signs with a secret),
    - the signature proves the card was not altered since printing,
    - the card carries an expiry, so old cards stop working on their own.

Because the card proves itself, the server needs no session storage —
that is what "stateless" means here, and why many server copies can all
accept the same card.

The four operations, in plain words
-----------------------------------
    create_access_token  -> print a short-lived "entry card" at login.
    create_refresh_token -> print a long-lived "renewal card" whose only
                            job is to get a new entry card later.
    decode_token         -> read a card back and prove it is genuine.
    verify_token_type    -> check the card is the right KIND (entry vs.
                            renewal), so a renewal card cannot be used
                            where an entry card is required.

Security note:
    Every card carries a "type" line. The signature alone cannot tell an
    entry card from a renewal card — only this line can.

Who uses this file
------------------
    Login flow   -> create_access_token / create_refresh_token
    Route guards -> decode_token / verify_token_type
                    (see auth_dependencies.py)

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# logging = writing to the application log.
import logging

# datetime/UTC/timedelta = stamp the card's "issued at" and "expires at".
from datetime import UTC, datetime, timedelta

# TYPE_CHECKING is only True while a type checker reads the file.
# Literal = a value that may be exactly one of a fixed set.
# get_args = list the allowed values of a Literal at runtime.
# cast = tell the type checker "we verified this already".
from typing import TYPE_CHECKING, Literal, cast, get_args

# UUID = the globally unique user id type.
from uuid import UUID

# The JWT library: jwt.encode/jwt.decode, plus its JWTError family.
from jose import JWTError, jwt

# The app's settings (signing secret, algorithm, token lifetimes).
from app.core.settings.settings import get_application_settings

# The typed identity a decoded card becomes.
from app.schemas.auth_schema import AuthTokenPayload, UserRole

# The set of all known platform role names.
from app.schemas.role_hierarchy import ALL_PLATFORM_ROLES

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# The two kinds of card: an "access" entry card and a "refresh" renewal
# card. The type checker then rejects any other string.
TokenType = Literal["access", "refresh"]


def _build_token_claims(
    user_id: UUID,
    role: str,
    token_type: TokenType,
    expires_at: datetime,
) -> dict[str, object]:
    """Assemble the standard lines printed on every card.

    Building every card through this one helper keeps the entry card and
    the renewal card identical in shape — only their "type" line and
    expiry differ — so the two can never drift apart.
    """
    now = datetime.now(UTC)
    return {
        "user_id": str(user_id),  # who the card belongs to
        "role": role,  # their clearance level
        "type": token_type,  # "access" (entry) or "refresh" (renewal)
        "exp": expires_at,  # when the card stops working
        "iat": now,  # when the card was printed
    }


def _assert_valid_role(role: str) -> None:
    """Refuse to print a card with a clearance level that does not exist."""
    if role not in ALL_PLATFORM_ROLES:
        raise ValueError(
            f"Role {role!r} is not valid. Must be one of: {sorted(ALL_PLATFORM_ROLES)}"
        )


def create_access_token(user_id: UUID, role: str) -> str:
    """Print a signed, short-lived entry card.

    Called once, at login. From then on, the client attaches this card to
    every API call as ``Authorization: Bearer <token>``, and the route
    guards read it back.

    Args:
        user_id: Who the card belongs to.
        role: Their clearance level (developer, operator, admin, owner).

    Returns:
        The signed JWT string.

    Raises:
        ValueError: The role is not a known platform role.
        JWTError: Signing failed.
    """
    # Auth Stage 1 / Sub-stage 1.1: mint the short-lived token the client
    # will attach to every API call from now on. Called once, at login.
    # What next: the client stores this and sends it as
    # `Authorization: Bearer <token>` on every request; Auth Stage 2 reads it.
    _assert_valid_role(role)  # no card with a made-up clearance level
    settings = get_application_settings()
    # Entry cards live for hours, not days.
    expires_at = datetime.now(UTC) + timedelta(hours=settings.jwt_access_token_expire_hours)
    claims = _build_token_claims(user_id, role, "access", expires_at)

    # Sign the card with the service's secret. Signature proves it was
    # printed here and has not been altered.
    token: str = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    logger.debug("Access token created", extra={"user_id": str(user_id), "role": role})
    return token


def create_refresh_token(user_id: UUID, role: str) -> str:
    """Print a signed, long-lived renewal card.

    The renewal card's ONLY job is to obtain a new entry card later,
    without asking for the password again. It is never sent to ordinary
    business endpoints — those demand an entry card.

    Args:
        user_id: Who the card belongs to.
        role: Their clearance level.

    Returns:
        The signed JWT refresh token string.

    Raises:
        ValueError: Refresh tokens are disabled, or the role is not a
            known platform role.
        JWTError: Signing failed.
    """
    # Auth Stage 1 / Sub-stage 1.2: mint the long-lived companion token
    # whose only job is to get a new access token later, without asking
    # for a password again. Never sent to ordinary business endpoints.
    # What next: the client holds this until the access token expires,
    # then exchanges it (login/refresh flow, outside this module) for a
    # fresh one.
    settings = get_application_settings()
    # Renewal cards can be switched off entirely by configuration.
    if not settings.jwt_refresh_enabled:
        raise ValueError("Refresh tokens are disabled. Set JWT_REFRESH_ENABLED=true to enable.")

    _assert_valid_role(role)
    # Renewal cards live for days, not hours.
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    claims = _build_token_claims(user_id, role, "refresh", expires_at)

    token: str = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    logger.debug("Refresh token created", extra={"user_id": str(user_id), "role": role})
    return token


def decode_token(token: str) -> AuthTokenPayload:
    """Read a card back and prove it is genuine.

    The JWT library verifies the signature and expiry. This function then
    checks that every required line is present and maps the raw values
    into the strongly typed AuthTokenPayload the rest of the app uses.

    Args:
        token: The raw JWT string from the Authorization header.

    Returns:
        The decoded, validated identity.

    Raises:
        JWTError: The token is expired, tampered, or malformed.
        ValueError: A required line is missing from the card.
    """
    # Auth Stage 1 / Sub-stage 1.3: read a token back and prove it is
    # genuine -- unmodified, unexpired, and signed with our secret. This
    # is called from Auth Stage 2 (`get_current_user`) on every protected
    # request.
    # What next: on success, the caller still must run `verify_token_type`
    # (Sub-stage 1.4) before trusting the payload for a specific purpose.
    settings = get_application_settings()
    try:
        # Verify the signature and expiry with our secret and algorithm.
        decoded_claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        # Tampered, expired, or not signed by us. Log the failure TYPE
        # (never the token) and let the caller turn it into a 401.
        logger.warning("JWT validation failed", extra={"error_type": type(exc).__name__})
        raise

    # Turn the decoded dict into a plain mapping of string -> value.
    claims: Mapping[str, object] = {
        str(claim_name): claim_value for claim_name, claim_value in decoded_claims.items()
    }
    # Every card must carry these five lines. Missing one = unusable card.
    for required_claim in ("user_id", "role", "type", "exp", "iat"):
        if required_claim not in claims:
            raise ValueError(f"Token is missing required claim: {required_claim!r}")

    # Prove the two enum-like lines carry KNOWN values before building
    # the payload. (The casts below then only tell the type checker we
    # checked — which is what makes them honest.) An unknown value is
    # reported with a clear message here, and — like every ValueError in
    # this function — the caller maps it to a 401.
    role_value = str(claims["role"])
    if role_value not in get_args(UserRole):
        raise ValueError(f"Token carries an unknown role: {role_value!r}")
    token_type_value = str(claims["type"])
    if token_type_value not in get_args(TokenType):
        raise ValueError(f"Token carries an unknown token type: {token_type_value!r}")

    return AuthTokenPayload(
        user_id=UUID(str(claims["user_id"])),
        role=cast("UserRole", role_value),
        token_type=cast("TokenType", token_type_value),
        # The "exp"/"iat" values came from the token's JSON. The casts
        # tell the type checker they are numbers; if one is NOT, int()
        # raises ValueError, which the caller maps to a 401 — exactly as
        # before the casts existed.
        expires_at=datetime.fromtimestamp(int(cast("int", claims["exp"])), tz=UTC),
        issued_at=datetime.fromtimestamp(int(cast("int", claims["iat"])), tz=UTC),
    )


def verify_token_type(payload: AuthTokenPayload, expected_token_type: TokenType) -> None:
    """Check the card is the right KIND for this use.

    This is what stops "token confusion": a renewal card being waved at a
    door that demands an entry card (or the reverse). The signature alone
    cannot tell the two apart — only the "type" line can.

    Args:
        payload: The decoded card from ``decode_token``.
        expected_token_type: Either ``"access"`` or ``"refresh"``.

    Raises:
        ValueError: The card's type line does not match what is expected.
    """
    # Auth Stage 1 / Sub-stage 1.4: stop a leaked refresh token from being
    # used as if it were an access token (or vice versa) -- the JWT
    # signature alone can't tell the two apart, only this claim check can.
    # What next: if this passes, Auth Stage 2 treats the payload as a
    # fully trusted identity for the rest of the request.
    if payload.token_type != expected_token_type:
        raise ValueError(
            f"Token type mismatch: expected {expected_token_type!r}, got {payload.token_type!r}"
        )
