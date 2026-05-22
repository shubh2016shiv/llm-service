"""
Authentication Schemas
======================

Typed models that define the shape of user identity and authorization data
that flows through the system after a user logs in.

TL;DR for new developers:
    When a user logs in, the system creates a JWT token containing their
    user ID and role. ``AuthTokenPayload`` is the Python object that
    represents that decoded token inside route handlers. When a user calls
    an inference endpoint, the authorization system checks their access and
    produces an ``InferenceAccessContext`` — a frozen snapshot of which
    tenant, deployment, provider, and model they are allowed to use. Both
    models are immutable (frozen) so no part of the system can accidentally
    change them after they are created.

Enterprise Pattern: Security Context Contract Pattern
    Authenticated identity and route-authorization data are passed as
    immutable, explicit objects instead of ad-hoc dictionaries.

Author: Shubham Singh
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Canonical set of roles in ascending privilege order.
# Higher roles inherit all permissions of lower roles.
UserRole = Literal["developer", "operator", "admin", "owner"]
TenantRole = Literal["owner", "admin", "operator", "developer", "viewer"]


class AuthTokenPayload(BaseModel):
    """Decoded JWT access or refresh token payload.

    Produced by ``decode_token`` and injected into route handlers
    via ``get_current_user``. Never construct this manually.
    """

    model_config = ConfigDict(frozen=True)

    user_id: UUID = Field(description="Unique identifier of the authenticated user.")
    role: UserRole = Field(description="Role that determines endpoint access level.")
    token_type: Literal["access", "refresh"] = Field(
        description="Token kind — prevents refresh tokens from authorising API calls."
    )
    expires_at: datetime = Field(description="UTC datetime at which the token expires.")
    issued_at: datetime = Field(description="UTC datetime at which the token was issued.")


class InferenceAccessContext(BaseModel):
    """Authorized tenant context for one inference route.

    Contains only routing authorization metadata. It intentionally excludes
    secret references and plaintext credentials so it is safe to cache.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID = Field(description="Tenant the caller is authorized to invoke under.")
    user_id: UUID = Field(description="Authenticated user receiving inference access.")
    deployment_key: str = Field(description="Tenant-scoped deployment route key.")
    deployment_id: UUID = Field(description="Resolved tenant deployment identifier.")
    provider_id: UUID = Field(description="Provider catalog identifier from the deployment.")
    model_id: UUID = Field(description="Model catalog identifier from the deployment.")
    tenant_role: TenantRole = Field(description="Caller role inside the tenant.")
    entitlement_id: UUID = Field(description="Active entitlement granting this route.")


class AuthTokenResponse(BaseModel):
    """Response body returned after successful login.

    Follows the OAuth 2.0 bearer token response convention.
    """

    model_config = ConfigDict(frozen=True)

    access_token: str = Field(description="Signed JWT access token.")
    token_type: str = Field(default="bearer", description="Always 'bearer'.")
    expires_in_seconds: int = Field(description="Number of seconds until the access token expires.")
