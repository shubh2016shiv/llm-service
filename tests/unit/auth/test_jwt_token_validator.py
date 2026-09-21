"""Security-contract tests for JWT access-token validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError, jwt

from app.auth.auth_dependencies import RoleGuard, get_current_user
from app.auth.jwt_token_validator import validate_access_token

if TYPE_CHECKING:
    from app.core.settings.settings import ApplicationSettings

USER_ID = UUID("10000000-0000-0000-0000-000000000099")


def _encode_claims(settings: ApplicationSettings, **overrides: object) -> str:
    """Sign a controlled upstream token for one validation scenario."""
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(USER_ID),
        "role": "developer",
        "type": "access",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "nbf": now,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": str(uuid4()),
    }
    for claim_name, claim_value in overrides.items():
        if claim_value is None:
            claims.pop(claim_name, None)
        else:
            claims[claim_name] = claim_value
    return jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def test_validate_access_token_returns_trusted_identity(
    test_settings: ApplicationSettings,
) -> None:
    """A complete token becomes the small identity object authorization uses."""
    payload = validate_access_token(_encode_claims(test_settings, role="admin"))

    assert payload.user_id == USER_ID
    assert payload.role == "admin"
    assert isinstance(payload.token_id, UUID)


@pytest.mark.parametrize("missing_claim", ["sub", "nbf", "iss", "aud", "jti"])
def test_validate_access_token_rejects_missing_registered_claim(
    test_settings: ApplicationSettings,
    missing_claim: str,
) -> None:
    """Every identity and replay-audit claim is mandatory, even when signed."""
    with pytest.raises((JWTError, ValueError)):
        validate_access_token(_encode_claims(test_settings, **{missing_claim: None}))


@pytest.mark.parametrize(
    ("claim_name", "claim_value"),
    [("iss", "wrong-issuer"), ("aud", "another-service")],
)
def test_validate_access_token_rejects_wrong_trust_boundary(
    test_settings: ApplicationSettings,
    claim_name: str,
    claim_value: str,
) -> None:
    """A valid signature cannot make a token for another service acceptable."""
    with pytest.raises(JWTError):
        validate_access_token(_encode_claims(test_settings, **{claim_name: claim_value}))


def test_validate_access_token_rejects_tampered_signature(
    test_settings: ApplicationSettings,
) -> None:
    """Changing signed bytes invalidates the token."""
    with pytest.raises(JWTError):
        validate_access_token(_encode_claims(test_settings) + "tampered")


def test_validate_access_token_rejects_unknown_role(
    test_settings: ApplicationSettings,
) -> None:
    """A signer cannot introduce a role the authorization model does not know."""
    with pytest.raises(ValueError, match="unknown role"):
        validate_access_token(_encode_claims(test_settings, role="superuser"))


def test_validate_access_token_rejects_refresh_token(
    test_settings: ApplicationSettings,
) -> None:
    """This resource server accepts access tokens only."""
    with pytest.raises(ValueError, match="not an access token"):
        validate_access_token(_encode_claims(test_settings, type="refresh"))


def test_validate_access_token_rejects_excessive_lifetime(
    test_settings: ApplicationSettings,
) -> None:
    """A compromised issuer cannot create a token valid beyond local policy."""
    too_late = datetime.now(UTC) + timedelta(seconds=test_settings.jwt_max_token_age_seconds + 60)

    with pytest.raises(ValueError, match="Token lifetime"):
        validate_access_token(_encode_claims(test_settings, exp=too_late))


@pytest.mark.asyncio
async def test_get_current_user_rejects_refresh_token(
    test_settings: ApplicationSettings,
) -> None:
    """The FastAPI boundary maps an unusable signed token to HTTP 401."""
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=_encode_claims(test_settings, type="refresh"),
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(credentials)

    assert excinfo.value.status_code == 401


def test_role_guard_rejects_insufficient_role(test_settings: ApplicationSettings) -> None:
    """An endpoint guard stops a caller outside its permitted role set."""
    guard = RoleGuard(["admin"])
    caller = validate_access_token(_encode_claims(test_settings, role="developer"))

    with pytest.raises(HTTPException) as excinfo:
        guard(caller)

    assert excinfo.value.status_code == 403
