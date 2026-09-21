"""Explicit response contracts for the management API.

Database rows are dictionaries, but an HTTP response is a public contract.
Returning an unrestricted dictionary would let a newly added database column
silently appear in the API. That is dangerous for hashes, secret references,
and internal audit fields.

Each model below is therefore an allow-list. ``extra="forbid"`` makes a
projection mismatch fail loudly instead of leaking an unexpected field. The
generic page keeps pagination uniform while preserving the concrete item type
in generated OpenAPI documentation.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field

from app.schemas.auth_schema import TenantRole, UserRole
from app.schemas.enums import (
    ModelLifecycleStatus,
    OperationType,
    ProviderCatalogAuthMode,
    ProviderCatalogType,
    TenantDeploymentStatus,
    TenantLifecycleStatus,
    TenantMembershipStatus,
    TenantSubscriptionTier,
    UserAccountStatus,
    UserEntitlementStatus,
)

JsonObject = dict[str, object]
class ManagementResponse(BaseModel):
    """Shared fail-closed behavior for database-backed API responses."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PaginatedResponse[ResponseItem: ManagementResponse](BaseModel):
    """A bounded page whose item schema remains visible in generated docs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[ResponseItem]
    total: int = Field(ge=0, description="Total matching rows across every page.")
    limit: int = Field(ge=1, le=1000, description="Maximum rows requested.")
    offset: int = Field(ge=0, description="Rows skipped before this page.")


class ProviderResponse(ManagementResponse):
    """Safe provider-catalog projection returned to API clients."""

    provider_id: UUID
    provider_name: str
    display_name: str
    provider_type: ProviderCatalogType
    auth_mode: ProviderCatalogAuthMode
    default_api_endpoint_url: str | None
    supported_operations: list[OperationType]
    is_active: bool
    provider_metadata: JsonObject
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ModelResponse(ManagementResponse):
    """Safe model-catalog projection returned to API clients."""

    model_id: UUID
    provider_id: UUID
    model_name: str
    model_version: str | None
    display_name: str | None
    supported_operations: list[OperationType]
    context_window_tokens: int | None
    max_output_tokens: int | None
    default_temperature: float
    default_top_p: float
    pricing_metadata: JsonObject
    model_metadata: JsonObject
    status: ModelLifecycleStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TenantResponse(ManagementResponse):
    """Tenant limits and lifecycle data safe for the management API."""

    tenant_id: UUID
    tenant_name: str
    tenant_slug: str
    status: TenantLifecycleStatus
    tier: TenantSubscriptionTier
    rate_limit_requests_per_minute: int
    rate_limit_tokens_per_minute: int
    rate_limit_concurrent_requests: int
    allowed_provider_names: list[str] | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class UserResponse(ManagementResponse):
    """Public user projection; password hashes are intentionally impossible."""

    user_id: UUID
    username: str
    email: EmailStr
    first_name: str
    last_name: str
    platform_role: UserRole
    status: UserAccountStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MembershipResponse(ManagementResponse):
    """One user's role and lifecycle state within a tenant."""

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    tenant_role: TenantRole
    status: TenantMembershipStatus
    created_by_user_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DeploymentResponse(ManagementResponse):
    """Safe deployment projection with no secret-backend reference."""

    deployment_id: UUID
    tenant_id: UUID
    provider_id: UUID
    model_id: UUID
    deployment_key: str
    deployment_name: str
    status: TenantDeploymentStatus
    api_endpoint_url: str
    cloud_provider: str | None
    cloud_region: str | None
    provider_deployment_name: str | None
    token_capacity_limit: int
    token_lock_duration_seconds: int
    timeout_seconds: float | None
    max_retries: int | None
    default_temperature: float
    default_top_p: float
    default_max_output_tokens: int | None
    is_default: bool
    routing_priority: int
    extra_headers: JsonObject
    extra_config: JsonObject
    created_by_user_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EntitlementResponse(ManagementResponse):
    """Safe user-entitlement projection with credentials excluded."""

    entitlement_id: UUID
    tenant_id: UUID
    user_id: UUID
    deployment_key: str
    provider_id: UUID
    model_id: UUID
    entitlement_name: str
    status: UserEntitlementStatus
    api_endpoint_url: str
    cloud_provider: str | None
    cloud_region: str | None
    provider_deployment_name: str | None
    extra_config: JsonObject
    created_by_user_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
