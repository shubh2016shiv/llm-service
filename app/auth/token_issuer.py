"""JWT access-token issuance for this service's own sign-in flow.

Architecture:
    SignInService -> issue_access_token -> signed bearer token -> dashboard
                                                |
                                                v
                                     validate_access_token (the mirror)

Why issuance lives here now
---------------------------
``jwt_token_validator`` was written on the premise that a separate identity
service would mint tokens and this one would only verify them. No such service
exists in this repository, and the dashboard needs a real sign-in, so the
choice was between adding issuance here or standing up a third service purely
to sign a claim set this service already defines.

Issuance was placed here deliberately, and the risk that premise guarded
against is handled by construction: this module builds exactly the claim set
``_REQUIRED_CLAIMS`` demands, reads the same settings object for secret,
algorithm, issuer and audience, and derives the token lifetime from
``access_token_ttl_seconds``, which the settings composition root refuses to
let exceed ``jwt_max_token_age_seconds``. The two halves cannot drift apart
without a test or a startup check failing first.

Dependencies:
    - app.core.settings.settings — the single source of JWT signing parameters.
    - app.auth.jwt_token_validator — the verifying counterpart; read both
      together when changing either.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID, uuid4

from jose import jwt

from app.core.settings.settings import get_application_settings

if TYPE_CHECKING:
    from app.schemas.auth_schema import UserRole

logger = logging.getLogger(__name__)


class IssuedAccessToken(NamedTuple):
    """One signed token plus the metadata a client needs to manage it.

    ``expires_at`` is returned rather than left for the client to decode, so
    the dashboard can schedule a re-authentication prompt without parsing JWTs.
    """

    token: str
    expires_at: datetime
    issued_at: datetime


def issue_access_token(*, user_id: UUID, role: UserRole) -> IssuedAccessToken:
    """Sign one access token carrying a caller's identity and platform role.

    The claim set mirrors ``validate_access_token``'s ``_REQUIRED_CLAIMS``
    exactly: sub, role, type, exp, iat, nbf, iss, aud, jti.

    Args:
        user_id: Existing ``users.user_id`` the token speaks for.
        role: Platform role granted to the session.

    Returns:
        IssuedAccessToken: The encoded token and its time window.

    Example:
        >>> issued = issue_access_token(user_id=uuid4(), role="admin")
        >>> issued.expires_at > issued.issued_at
        True
    """
    settings = get_application_settings()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.access_token_ttl_seconds)
    token_id = uuid4()

    claims = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        # nbf equals iat: this token is valid the moment it is signed. A future
        # nbf would make a freshly issued token unusable until clocks caught up.
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": str(token_id),
    }
    token: str = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    logger.info(
        "Access token issued",
        extra={
            "user_id": str(user_id),
            "role": role,
            "token_id": str(token_id),
            "ttl_seconds": settings.access_token_ttl_seconds,
        },
    )
    return IssuedAccessToken(token=token, expires_at=expires_at, issued_at=issued_at)
