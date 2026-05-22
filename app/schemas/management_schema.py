"""
Management API Schemas
======================

Pydantic request and response contracts for management endpoints (tenants,
users, providers, models, deployments, memberships, and entitlements).

Why are Create and Update separate models for the same entity?
    Creating a resource (POST) requires certain fields to be present — for
    example, a tenant must have a name. Updating a resource (PATCH) makes
    every field optional so callers can send only the fields they want to
    change. If both operations shared one model, PATCH would either force
    callers to resend unchanged data or reject valid partial updates. Keeping
    them separate prevents this class of bug entirely.

Enterprise Pattern: CRUD Contract Segregation Pattern
    Create and update operations use separate models to keep API behavior
    clear and prevent accidental field misuse.

Author: Shubham Singh
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

JsonObject = dict[str, object]
PlatformRole = Literal["owner", "admin", "operator", "developer"]
TenantRole = Literal["owner", "admin", "operator", "developer", "viewer"]
LifecycleStatus = Literal["active", "inactive", "suspended", "deleted"]


class MessageResponse(BaseModel):
    """Simple success response for non-empty confirmation payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(description="Human-readable operation result.")


class PaginatedResponse(BaseModel):
    """Generic paginated response shape for list endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[JsonObject] = Field(description="Page of returned resources.")
    total: int = Field(ge=0, description="Total matching resource count.")
    limit: int = Field(ge=1, description="Maximum rows requested.")
    offset: int = Field(ge=0, description="Rows skipped before this page.")


class ProviderCreateRequest(BaseModel):
    """Request body for registering a provider catalog entry."""

    model_config = ConfigDict(extra="forbid")

    provider_name: str = Field(min_length=1, examples=["openai"])
    display_name: str = Field(min_length=1, examples=["OpenAI"])
    provider_type: str = Field(min_length=1, examples=["direct_api"])
    auth_mode: str = Field(min_length=1, examples=["bearer_token"])
    supported_operations: list[str] = Field(min_length=1, examples=[["chat", "embed"]])
    default_api_endpoint_url: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    provider_metadata: JsonObject | None = Field(default=None)


class ProviderUpdateRequest(BaseModel):
    """Request body for partially updating a provider."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1)
    default_api_endpoint_url: str | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    supported_operations: list[str] | None = Field(default=None, min_length=1)
    provider_metadata: JsonObject | None = Field(default=None)


class ModelCreateRequest(BaseModel):
    """Request body for registering a model under a provider."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(min_length=1, examples=["gpt-4o"])
    supported_operations: list[str] = Field(min_length=1, examples=[["chat"]])
    model_version: str | None = Field(default=None)
    display_name: str | None = Field(default=None)
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    pricing_metadata: JsonObject | None = Field(default=None)
    model_metadata: JsonObject | None = Field(default=None)
    status: Literal["active", "deprecated", "retired"] = Field(default="active")


class ModelUpdateRequest(BaseModel):
    """Request body for partially updating a model catalog entry."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None)
    status: Literal["active", "deprecated", "retired"] | None = Field(default=None)
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    default_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    default_top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    pricing_metadata: JsonObject | None = Field(default=None)
    model_metadata: JsonObject | None = Field(default=None)


class TenantCreateRequest(BaseModel):
    """Request body for creating a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant_name: str = Field(min_length=1)
    tenant_slug: str = Field(min_length=1, examples=["acme-corp"])
    tier: Literal["free", "starter", "professional", "enterprise"] = Field(default="free")
    status: Literal["active", "trial", "suspended", "deleted"] = Field(default="active")
    rate_limit_requests_per_minute: int = Field(default=1000, ge=1)
    rate_limit_tokens_per_minute: int = Field(default=100000, ge=1)
    rate_limit_concurrent_requests: int = Field(default=10, ge=1)
    allowed_provider_names: list[str] | None = Field(default=None)


class TenantUpdateRequest(BaseModel):
    """Request body for partially updating a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant_name: str | None = Field(default=None, min_length=1)
    tier: Literal["free", "starter", "professional", "enterprise"] | None = Field(default=None)
    status: Literal["active", "trial", "suspended", "deleted"] | None = Field(default=None)
    rate_limit_requests_per_minute: int | None = Field(default=None, ge=1)
    rate_limit_tokens_per_minute: int | None = Field(default=None, ge=1)
    rate_limit_concurrent_requests: int | None = Field(default=None, ge=1)
    allowed_provider_names: list[str] | None = Field(default=None)


class UserCreateRequest(BaseModel):
    """Request body for platform user creation."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1)
    email: EmailStr = Field(description="Unique email address.")
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    password: str = Field(min_length=12, description="Plaintext password, hashed in service.")
    platform_role: PlatformRole = Field(default="developer")
    status: LifecycleStatus = Field(default="active")


class UserUpdateRequest(BaseModel):
    """Request body for partially updating a user."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = Field(default=None)
    platform_role: PlatformRole | None = Field(default=None)
    status: LifecycleStatus | None = Field(default=None)


class MembershipCreateRequest(BaseModel):
    """Request body for adding a user to a tenant."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID = Field(description="User to add to the tenant.")
    tenant_role: TenantRole = Field(default="developer")
    status: Literal["active", "suspended", "inactive"] = Field(default="active")


class MembershipUpdateRequest(BaseModel):
    """Request body for updating tenant membership role or status."""

    model_config = ConfigDict(extra="forbid")

    tenant_role: TenantRole | None = Field(default=None)
    status: Literal["active", "suspended", "inactive"] | None = Field(default=None)


class DeploymentCreateRequest(BaseModel):
    """Request body for creating a tenant deployment."""

    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    model_id: UUID
    deployment_key: str = Field(min_length=1)
    deployment_name: str = Field(min_length=1)
    api_endpoint_url: str = Field(min_length=1)
    secret_reference: str = Field(min_length=1)
    token_capacity_limit: int = Field(ge=1)
    status: Literal["active", "inactive", "maintenance"] = Field(default="active")
    cloud_provider: str | None = Field(default=None)
    cloud_region: str | None = Field(default=None)
    provider_deployment_name: str | None = Field(default=None)
    token_lock_duration_seconds: int = Field(default=70, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    default_max_output_tokens: int | None = Field(default=None, ge=1)
    is_default: bool = Field(default=False)
    routing_priority: int = Field(default=0, ge=0)
    extra_headers: JsonObject | None = Field(default=None)
    extra_config: JsonObject | None = Field(default=None)


class DeploymentUpdateRequest(BaseModel):
    """Request body for partially updating a tenant deployment."""

    model_config = ConfigDict(extra="forbid")

    deployment_name: str | None = Field(default=None, min_length=1)
    status: Literal["active", "inactive", "maintenance"] | None = Field(default=None)
    api_endpoint_url: str | None = Field(default=None, min_length=1)
    secret_reference: str | None = Field(default=None, min_length=1)
    cloud_provider: str | None = Field(default=None)
    cloud_region: str | None = Field(default=None)
    provider_deployment_name: str | None = Field(default=None)
    token_capacity_limit: int | None = Field(default=None, ge=1)
    token_lock_duration_seconds: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    default_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    default_top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    default_max_output_tokens: int | None = Field(default=None, ge=1)
    is_default: bool | None = Field(default=None)
    routing_priority: int | None = Field(default=None, ge=0)
    extra_headers: JsonObject | None = Field(default=None)
    extra_config: JsonObject | None = Field(default=None)


class EntitlementCreateRequest(BaseModel):
    """Request body for creating a user entitlement."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    deployment_key: str = Field(min_length=1)
    provider_id: UUID
    model_id: UUID
    entitlement_name: str = Field(min_length=1)
    api_endpoint_url: str = Field(min_length=1)
    secret_reference: str = Field(min_length=1)
    status: Literal["active", "inactive", "revoked"] = Field(default="active")
    cloud_provider: str | None = Field(default=None)
    cloud_region: str | None = Field(default=None)
    provider_deployment_name: str | None = Field(default=None)
    extra_config: JsonObject | None = Field(default=None)


class EntitlementUpdateRequest(BaseModel):
    """Request body for partially updating a user entitlement."""

    model_config = ConfigDict(extra="forbid")

    api_endpoint_url: str | None = Field(default=None, min_length=1)
    secret_reference: str | None = Field(default=None, min_length=1)
    status: Literal["active", "inactive", "revoked"] | None = Field(default=None)
    cloud_provider: str | None = Field(default=None)
    cloud_region: str | None = Field(default=None)
    provider_deployment_name: str | None = Field(default=None)
    extra_config: JsonObject | None = Field(default=None)


class ResourceResponse(BaseModel):
    """Flexible read model for database-backed management rows."""

    model_config = ConfigDict(extra="allow", frozen=True)

    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
