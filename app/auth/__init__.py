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

Where this fits in the request pipeline (continues the "Stage N:M" labels
already used on the API routers -- prefixed "Auth Stage" here so the two
numbering schemes never collide):
    ENTRY POINT: ``get_current_user`` in ``auth_dependencies.py``. FastAPI
    calls it automatically for every route that declares it via ``Depends``
    -- there is no other way into this package; nothing calls it manually.

    Auth Stage 1 (jwt_token_validator.py) -- verify the upstream identity token.
    Auth Stage 2 (auth_dependencies.py)   -- per-request identity + role gate.
    Auth Stage 3 (authorization/tenant_access.py)       -- tenant checks for
        management APIs (users, tenants, deployments, entitlements).
    Auth Stage 4 (authorization/tenant_inference_auth.py) -- tenant +
        deployment + entitlement checks for inference APIs (chat/embed/rerank).
    Auth Stage 5 (authorization/authorization_grant_cache.py) -- caches Stage 4's answer so
        repeat inference calls skip the database round trip.

    What next: once this package returns a validated ``AuthTokenPayload`` (or,
    for inference routes, an ``InferenceAccessContext``), route handlers pass
    it straight into the service layer -- no further auth checks happen there.

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
from app.schemas.auth_schema import (
    AuthTokenPayload,
    InferenceAccessContext,
    UserRole,
)

__all__ = [
    "AuthTokenPayload",
    "InferenceAccessContext",
    "RoleGuard",
    "UserRole",
    "get_current_user",
    "require_admin",
    "require_developer",
    "require_operator",
    "require_owner",
]
