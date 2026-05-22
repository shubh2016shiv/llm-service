"""
Authorization Package
=====================

This package contains tenant-scoped authorization logic used after JWT
authentication succeeds.

Enterprise Pattern: Facade Pattern
    This ``__init__.py`` re-exports the main authorization classes so calling
    modules can import from ``app.auth.authorization`` without knowing file-level
    details.

Step-by-step relationship flow:
    1. JWT authentication resolves caller identity and platform role.
    2. ``TenantAccessService`` enforces tenant membership/admin checks for
       management APIs.
    3. ``TenantAuthorizationService`` evaluates inference route access using
       tenant, membership, deployment, and entitlement state.
    4. ``InferenceAuthorizationCache`` stores and invalidates successful grants
       to reduce repeated database reads on hot routes.

Author: Shubham Singh
"""

from app.auth.authorization.cache import (
    AuthorizationCacheBackend,
    AuthorizationVersionSnapshot,
    CachedInferenceAuthorization,
    InferenceAuthorizationCache,
)
from app.auth.authorization.tenant_access import TenantAccessService
from app.auth.authorization.tenant_inference_auth import TenantAuthorizationService

__all__ = [
    "AuthorizationCacheBackend",
    "AuthorizationVersionSnapshot",
    "CachedInferenceAuthorization",
    "InferenceAuthorizationCache",
    "TenantAccessService",
    "TenantAuthorizationService",
]
