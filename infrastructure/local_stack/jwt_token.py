"""Infrastructure-only JWT generation for the local admin dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from jose import jwt

if TYPE_CHECKING:
    from infrastructure.local_stack.environment import LocalInfrastructureEnvironment

_PLATFORM_ROLES = frozenset({"developer", "operator", "admin", "owner"})


def generate_dashboard_jwt(environment: LocalInfrastructureEnvironment) -> str:
    """Mint a raw access token matching the backend's local JWT contract.

    This helper deliberately lives in ``infrastructure`` and does not import
    backend authentication code. Its inputs all come from the infra-managed
    local ``.env`` values used by Docker Compose.
    """
    if environment.dashboard_jwt_role not in _PLATFORM_ROLES:
        raise ValueError(
            "LOCAL_DASHBOARD_JWT_ROLE must be one of: " + ", ".join(sorted(_PLATFORM_ROLES))
        )

    now = datetime.now(UTC)
    claims = {
        "sub": str(environment.dashboard_jwt_user_id),
        "role": environment.dashboard_jwt_role,
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(hours=environment.jwt_access_token_expire_hours),
        "iss": environment.jwt_issuer,
        "aud": environment.jwt_audience,
        "jti": str(uuid4()),
    }
    token: str = jwt.encode(
        claims,
        environment.jwt_secret_key,
        algorithm=environment.jwt_algorithm,
    )
    return token
