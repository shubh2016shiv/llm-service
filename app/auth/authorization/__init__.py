"""
Authorization Package
=====================

This package contains tenant-scoped authorization logic used after JWT
authentication succeeds.

Enterprise Pattern: Facade Pattern
    This ``__init__.py`` re-exports the main authorization classes so calling
    modules can import from ``app.auth.authorization`` without knowing file-level
    details.

Typical flow:
    JWT validated -> authorization service checks tenant/deployment access
    -> inference route proceeds only when access is allowed.

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
