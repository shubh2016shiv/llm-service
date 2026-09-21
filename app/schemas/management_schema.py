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

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
)

from app.core.settings.models.provider_config import AuthMode
from app.core.settings.url_validation import validate_provider_endpoint_url
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
from app.schemas.model_constraints import (
    MAX_TEMPERATURE,
    MAX_TOP_P,
    MIN_TEMPERATURE,
    MIN_TOP_P,
)

ProviderEndpointUrl = Annotated[str, AfterValidator(validate_provider_endpoint_url)]
TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
OptionalTrimmedText = TrimmedText | None
ProviderName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
KebabIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
    ),
]
JsonObject = dict[str, object]
# Aliases, not re-declarations. These used to be independent `Literal[...]`
# copies of the same role vocabulary defined in auth_schema, so a role added
# for the API surface could silently disagree with the one the authorization
# layer enforces. Aliasing makes that impossible: there is one declaration per
# namespace, in auth_schema, ranked in role_hierarchy.
PlatformRole = UserRole


class ProviderCreateRequest(BaseModel):
    """Request body for registering a provider catalog entry."""

    model_config = ConfigDict(extra="forbid")

    provider_name: ProviderName = Field(examples=["openai"])
    display_name: TrimmedText = Field(examples=["OpenAI"])
    provider_type: ProviderCatalogType = Field(default=ProviderCatalogType.DIRECT_API)
    auth_mode: ProviderCatalogAuthMode = Field(default=ProviderCatalogAuthMode.BEARER_TOKEN)
    supported_operations: list[OperationType] = Field(
        min_length=1,
        max_length=len(OperationType),
        examples=[["chat", "embed"]],
    )
    default_api_endpoint_url: ProviderEndpointUrl | None = Field(default=None)
    is_active: bool = Field(default=True)
    provider_metadata: JsonObject | None = Field(default=None)


class ProviderTemplateModel(BaseModel):
    """One model entry from a provider's static runtime config (YAML)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    context_window_tokens: int
    max_output_tokens: int
    supported_operations: list[str]


class ProviderTemplate(BaseModel):
    """A known provider's runtime config, offered to prefill the create forms.

    Sourced from ``config/providers/*.yaml`` — the same files
    ``route_resolution.py``'s Gate 4 reads when authorizing a real inference
    call. Registering a provider or model through the dashboard writes to
    PostgreSQL only; nothing there requires (or checks for) a matching YAML
    file, so a name typo, or a provider with no YAML entry yet, silently
    creates deployments that fail on the first real request with
    "Provider config not found" or "Model not supported" — errors that
    surface long after the mistake was made.

    Exposing the known-good set here lets the create forms prefill from, and
    warn against, the registry that is actually enforced at request time. It
    Provider creation validates against this same registry, so every persisted
    provider is immediately routable instead of failing on its first request.

    Security note: carries endpoints, auth *modes*, and model limits only.
    Credentials live in the secret backend and are never part of this payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    suggested_provider_type: ProviderCatalogType
    # A runtime template can legitimately advertise ``none`` for a private
    # endpoint. This is not the same vocabulary as the database catalog,
    # whose ProviderCatalogAuthMode also includes ``custom``.
    auth_mode: AuthMode
    default_api_endpoint_url: str
    supported_operations: list[str]
    models: list[ProviderTemplateModel]


class ProviderTemplateListResponse(BaseModel):
    """Response envelope for ``GET /api/v1/providers/runtime-templates``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[ProviderTemplate]


class ProviderUpdateRequest(BaseModel):
    """Request body for partially updating a provider."""

    model_config = ConfigDict(extra="forbid")

    display_name: OptionalTrimmedText = None
    default_api_endpoint_url: ProviderEndpointUrl | None = None
    is_active: bool | None = Field(default=None)
    supported_operations: list[OperationType] | None = Field(
        default=None,
        min_length=1,
        max_length=len(OperationType),
    )
    provider_metadata: JsonObject | None = Field(default=None)


class ModelCreateRequest(BaseModel):
    """Request body for registering a model under a provider."""

    model_config = ConfigDict(extra="forbid")

    model_name: TrimmedText = Field(examples=["gpt-4o"])
    supported_operations: list[OperationType] = Field(
        min_length=1,
        max_length=len(OperationType),
        examples=[["chat"]],
    )
    model_version: OptionalTrimmedText = None
    display_name: OptionalTrimmedText = None
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    default_temperature: float = Field(default=0.7, ge=MIN_TEMPERATURE, le=MAX_TEMPERATURE)
    default_top_p: float = Field(default=1.0, ge=MIN_TOP_P, le=MAX_TOP_P)
    pricing_metadata: JsonObject | None = Field(default=None)
    model_metadata: JsonObject | None = Field(default=None)
    status: ModelLifecycleStatus = Field(default=ModelLifecycleStatus.ACTIVE)


class ModelUpdateRequest(BaseModel):
    """Request body for partially updating a model catalog entry."""

    model_config = ConfigDict(extra="forbid")

    display_name: OptionalTrimmedText = None
    status: ModelLifecycleStatus | None = Field(default=None)
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    default_temperature: float | None = Field(default=None, ge=MIN_TEMPERATURE, le=MAX_TEMPERATURE)
    default_top_p: float | None = Field(default=None, ge=MIN_TOP_P, le=MAX_TOP_P)
    pricing_metadata: JsonObject | None = Field(default=None)
    model_metadata: JsonObject | None = Field(default=None)


class TenantCreateRequest(BaseModel):
    """Request body for creating a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant_name: TrimmedText
    tenant_slug: KebabIdentifier = Field(examples=["acme-corp"])
    tier: TenantSubscriptionTier = Field(default=TenantSubscriptionTier.FREE)
    status: TenantLifecycleStatus = Field(default=TenantLifecycleStatus.ACTIVE)
    rate_limit_requests_per_minute: int = Field(default=1000, ge=1)
    rate_limit_tokens_per_minute: int = Field(default=100000, ge=1)
    rate_limit_concurrent_requests: int = Field(default=10, ge=1)
    allowed_provider_names: list[ProviderName] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )


class TenantUpdateRequest(BaseModel):
    """Request body for partially updating a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant_name: OptionalTrimmedText = None
    tier: TenantSubscriptionTier | None = Field(default=None)
    status: TenantLifecycleStatus | None = Field(default=None)
    rate_limit_requests_per_minute: int | None = Field(default=None, ge=1)
    rate_limit_tokens_per_minute: int | None = Field(default=None, ge=1)
    rate_limit_concurrent_requests: int | None = Field(default=None, ge=1)
    allowed_provider_names: list[ProviderName] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )


class UserCreateRequest(BaseModel):
    """Request body for platform user creation."""

    model_config = ConfigDict(extra="forbid")

    username: TrimmedText
    email: EmailStr = Field(description="Unique email address.")
    first_name: TrimmedText
    last_name: TrimmedText
    password: SecretStr = Field(
        min_length=12,
        max_length=1024,
        description="Plaintext password, redacted in memory and hashed in the service.",
    )
    platform_role: PlatformRole = Field(default="developer")
    status: UserAccountStatus = Field(default=UserAccountStatus.ACTIVE)


class UserUpdateRequest(BaseModel):
    """Request body for partially updating a user."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = Field(default=None)
    platform_role: PlatformRole | None = Field(default=None)
    status: UserAccountStatus | None = Field(default=None)


class MembershipCreateRequest(BaseModel):
    """Request body for adding a user to a tenant."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID = Field(description="User to add to the tenant.")
    tenant_role: TenantRole = Field(default="developer")
    status: TenantMembershipStatus = Field(default=TenantMembershipStatus.ACTIVE)


class MembershipUpdateRequest(BaseModel):
    """Request body for updating tenant membership role or status."""

    model_config = ConfigDict(extra="forbid")

    tenant_role: TenantRole | None = Field(default=None)
    status: TenantMembershipStatus | None = Field(default=None)


class BearerCredential(BaseModel):
    """A single API key sent as a bearer token or a custom header value."""

    model_config = ConfigDict(extra="forbid")

    auth_mode: Literal[ProviderCatalogAuthMode.BEARER_TOKEN, ProviderCatalogAuthMode.API_KEY_HEADER]
    api_key: SecretStr = Field(
        min_length=1,
        max_length=16_384,
        description="Provider API key, redacted from model repr and logs.",
    )


# The provider layer currently consumes API keys plus AWS ambient identity.
# OAuth exchange and arbitrary custom fields are intentionally not accepted:
# exposing unimplemented credential shapes would create records that can never
# authenticate at inference time.
CredentialInput = BearerCredential


class DeploymentCreateRequest(BaseModel):
    """Request body for creating a tenant deployment."""

    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    model_id: UUID
    deployment_key: KebabIdentifier = Field(
        description="Lowercase, hyphen-separated (kebab-case) — matches the tenant_deployments CHECK constraint.",
        examples=["gpt-4o-prod"],
    )
    deployment_name: TrimmedText
    api_endpoint_url: ProviderEndpointUrl
    credential: CredentialInput | None = Field(
        default=None,
        description=(
            "Provider credential; written to the configured secret backend, never stored "
            "as plaintext. Omit only when the provider authenticates via ambient "
            "infrastructure credentials (for example, an AWS IAM role for an aws_sigv4 "
            "provider such as Bedrock)."
        ),
    )
    token_capacity_limit: int = Field(ge=1)
    status: TenantDeploymentStatus = Field(default=TenantDeploymentStatus.ACTIVE)
    cloud_provider: OptionalTrimmedText = None
    cloud_region: OptionalTrimmedText = None
    provider_deployment_name: OptionalTrimmedText = None
    token_lock_duration_seconds: int = Field(default=70, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    default_temperature: float = Field(default=0.7, ge=MIN_TEMPERATURE, le=MAX_TEMPERATURE)
    default_top_p: float = Field(default=1.0, ge=MIN_TOP_P, le=MAX_TOP_P)
    default_max_output_tokens: int | None = Field(default=None, ge=1)
    is_default: bool = Field(default=False)
    routing_priority: int = Field(default=0, ge=0)
    extra_headers: JsonObject | None = Field(default=None)
    extra_config: JsonObject | None = Field(default=None)


class DeploymentUpdateRequest(BaseModel):
    """Request body for partially updating a tenant deployment."""

    model_config = ConfigDict(extra="forbid")

    deployment_name: OptionalTrimmedText = None
    status: TenantDeploymentStatus | None = Field(default=None)
    api_endpoint_url: ProviderEndpointUrl | None = None
    credential: CredentialInput | None = Field(
        default=None,
        description=(
            "Replacement provider credential. The service writes it to a new secret version; "
            "clients never submit or choose secret backend paths. Explicit null selects ambient "
            "credentials and is valid only for aws_sigv4 providers."
        ),
    )
    cloud_provider: OptionalTrimmedText = None
    cloud_region: OptionalTrimmedText = None
    provider_deployment_name: OptionalTrimmedText = None
    token_capacity_limit: int | None = Field(default=None, ge=1)
    token_lock_duration_seconds: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    default_temperature: float | None = Field(default=None, ge=MIN_TEMPERATURE, le=MAX_TEMPERATURE)
    default_top_p: float | None = Field(default=None, ge=MIN_TOP_P, le=MAX_TOP_P)
    default_max_output_tokens: int | None = Field(default=None, ge=1)
    is_default: bool | None = Field(default=None)
    routing_priority: int | None = Field(default=None, ge=0)
    extra_headers: JsonObject | None = Field(default=None)
    extra_config: JsonObject | None = Field(default=None)


class EntitlementCreateRequest(BaseModel):
    """Request body for creating a user entitlement."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    deployment_key: KebabIdentifier
    provider_id: UUID
    model_id: UUID
    entitlement_name: TrimmedText
    api_endpoint_url: ProviderEndpointUrl
    credential: CredentialInput | None = Field(
        default=None,
        description=(
            "User-owned credential; written to the configured secret backend, never stored "
            "as plaintext. Omit only when the provider authenticates via ambient "
            "infrastructure credentials."
        ),
    )
    status: UserEntitlementStatus = Field(default=UserEntitlementStatus.ACTIVE)
    cloud_provider: OptionalTrimmedText = None
    cloud_region: OptionalTrimmedText = None
    provider_deployment_name: OptionalTrimmedText = None
    extra_config: JsonObject | None = Field(default=None)

class EntitlementUpdateRequest(BaseModel):
    """Request body for partially updating a user entitlement."""

    model_config = ConfigDict(extra="forbid")

    api_endpoint_url: ProviderEndpointUrl | None = None
    credential: CredentialInput | None = Field(
        default=None,
        description=(
            "Replacement user credential. Omit the field to keep the current credential; "
            "clients cannot directly choose a secret backend path."
        ),
    )
    status: UserEntitlementStatus | None = Field(default=None)
    cloud_provider: OptionalTrimmedText = None
    cloud_region: OptionalTrimmedText = None
    provider_deployment_name: OptionalTrimmedText = None
    extra_config: JsonObject | None = Field(default=None)
