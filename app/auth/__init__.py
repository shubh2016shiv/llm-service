"""
app/auth — JWT authentication and role-based access control.

Public surface for endpoint protection:

    from app.auth import require_developer, AuthTokenPayload

    @router.get("/protected")
    async def my_endpoint(
        current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    ) -> ...:
        ...

Role hierarchy (ascending privilege):
    developer < operator < admin < owner
"""

from app.auth.auth_dependencies import (
    RoleGuard,
    get_current_user,
    require_admin,
    require_developer,
    require_operator,
    require_owner,
)
from app.auth.jwt_token_service import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_token_type,
)
from app.schemas.auth_schema import AuthTokenPayload, AuthTokenResponse, UserRole

__all__ = [
    "AuthTokenPayload",
    "AuthTokenResponse",
    "RoleGuard",
    "UserRole",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "require_admin",
    "require_developer",
    "require_operator",
    "require_owner",
    "verify_token_type",
]
