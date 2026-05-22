"""
Authentication Package
======================

This package provides everything API routes need to verify who the caller is
and what that caller is allowed to do.

Enterprise Pattern: Facade Pattern
    Other modules import from ``app.auth`` instead of many internal files.
    This keeps imports simple and hides internal layout details.

How this package is used in routes:
    1) Route depends on ``get_current_user`` to validate JWT.
    2) Route adds a role guard such as ``require_admin``.
    3) Route receives ``AuthTokenPayload`` only after checks pass.

Role hierarchy (ascending privilege):
    developer < operator < admin < owner

Author: Shubham Singh
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
from app.schemas.auth_schema import (
    AuthTokenPayload,
    AuthTokenResponse,
    InferenceAccessContext,
    UserRole,
)

__all__ = [
    "AuthTokenPayload",
    "AuthTokenResponse",
    "InferenceAccessContext",
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
