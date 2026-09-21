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

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

# The two role vocabularies. The ORDER here is just a list of allowed
# words — the actual "who outranks whom" ranking lives in
# role_hierarchy.py, which is the single source of truth for privilege.
#
# UserRole = platform-wide roles (listed ascending privilege for
# readability): a developer is the lowest rung, owner the highest.
UserRole = Literal["developer", "operator", "admin", "owner"]
# TenantRole = roles INSIDE one tenant. It adds "viewer" (read-only) and
# has no meaningful order in this Literal — role_hierarchy.py ranks it.
TenantRole = Literal["owner", "admin", "operator", "developer", "viewer"]


class AuthTokenPayload(BaseModel):
    """Identity extracted from a fully validated JWT access token.

    Produced by ``validate_access_token`` and injected into route handlers
    via ``get_current_user``. Never construct this manually.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID = Field(description="Unique identifier of the authenticated user.")
    role: UserRole = Field(description="Role that determines endpoint access level.")
    token_id: UUID = Field(description="Unique JWT identifier used for audit correlation.")
    expires_at: AwareDatetime = Field(description="UTC datetime at which the token expires.")
    issued_at: AwareDatetime = Field(description="UTC datetime at which the token was issued.")

    @model_validator(mode="after")
    def validate_token_time_window(self) -> AuthTokenPayload:
        """Reject impossible tokens before authorization code can see them."""
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self


class InferenceAccessContext(BaseModel):
    """Authorized tenant context for one inference route.

    Contains only routing authorization metadata. It intentionally excludes
    secret references and plaintext credentials so it is safe to cache.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID = Field(description="Tenant the caller is authorized to invoke under.")
    user_id: UUID = Field(description="Authenticated user receiving inference access.")
    deployment_key: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description="Tenant-scoped deployment route key.",
    )
    deployment_id: UUID = Field(description="Resolved tenant deployment identifier.")
    provider_id: UUID = Field(description="Provider catalog identifier from the deployment.")
    model_id: UUID = Field(description="Model catalog identifier from the deployment.")
    tenant_role: TenantRole = Field(description="Caller role inside the tenant.")
    entitlement_id: UUID = Field(description="Active entitlement granting this route.")


class AuthorizationGrantVersions(BaseModel):
    """Version snapshot used to reject stale cached authorization grants."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # These sentinels preserve the existing cache protocol for scopes that have
    # never been invalidated and therefore do not yet have a Redis marker.
    tenant_version: str = Field(default="tenant:0", pattern=r"^tenant:\d+$")
    membership_version: str = Field(default="membership:0", pattern=r"^membership:\d+$")
    deployment_version: str = Field(default="deployment:0", pattern=r"^deployment:\d+$")
    route_version: str = Field(default="route:0", pattern=r"^route:\d+$")


class CachedAuthorizationGrant(BaseModel):
    """A saved authorization "yes" and the counter values it was decided against.

    Written by the grant cache (authorization_grant_cache.py) after a
    successful inference authorization, and read back on later requests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: InferenceAccessContext  # the "yes" itself
    versions: AuthorizationGrantVersions  # the counters it was checked against


class AuthorizationGrantLookup(BaseModel):
    """The result of one grant-cache read: a possible answer + the counters seen.

    ``context`` is the saved answer when one was valid, else None.
    ``observed_versions`` is what the reader saw this time (used to guard
    a later save), or None when no cache backend was configured.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: InferenceAccessContext | None
    observed_versions: AuthorizationGrantVersions | None
