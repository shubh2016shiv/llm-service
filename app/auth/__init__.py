"""
Authentication Package
======================

This package exposes the authentication and authorization entry points used by
API routes to answer two questions:
    1. Who is the caller? (identity verification via JWT)
    2. What may the caller do? (role and tenant-scope authorization)

Enterprise Pattern: Facade Pattern
    Other modules import from ``app.auth`` instead of many internal files.
    This keeps imports simple and hides internal layout details.

Step-by-step route relationship:
    1. A route uses ``get_current_user`` to decode and validate a bearer token.
    2. The same route can apply a role guard (for example, ``require_admin``)
       to enforce platform-level permissions.
    3. Service-layer authorization (tenant membership, deployment entitlement)
       runs only after these upstream checks pass.
    4. Route handlers receive a typed ``AuthTokenPayload`` that downstream
       services can trust as authenticated identity input.

Role hierarchy (ascending privilege):
    developer < operator < admin < owner

Why hierarchy matters:
    The code treats higher-privilege roles as supersets of lower-privilege
    access. For example, a route requiring ``operator`` also allows ``admin``
    and ``owner``.

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
