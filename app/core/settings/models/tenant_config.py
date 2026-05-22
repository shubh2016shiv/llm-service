"""
Tenant & Deployment Configuration Models — Runtime, DB-sourced, per-org configs.

Unlike provider/global configs (static YAML), these are loaded from PostgreSQL
and cached in Redis. They can change at runtime without restart.

Architecture:
-------------
    PostgreSQL (source of truth)
          │
          ▼
    ConfigLoader.load_deployment_config(tenant_id, deployment_id)
          │
          ├──► DeploymentConfig   (per tenant+deployment — API key ref, model, timeouts)
          ├──► UserEntitlementConfig (per user personal API key)
          └──► TenantConfig       (org-level rate limits, allowed providers)

Step-by-step relation:
    1. Management services write tenant/deployment rows to PostgreSQL.
    2. Routing layer reads these rows and validates into frozen models.
    3. Redis caches serialized config for fast request-path access.
    4. Cache invalidation on management updates keeps runtime decisions fresh.

Thread-safety: All models are frozen. Dynamic request params (temperature,
trace_id) are NEVER stored here — they are local to the request call frame.

Dependencies:
    - pydantic >= 2.0

Author: Shubham Singh
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant organisation."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    DELETED = "deleted"


class TenantTier(StrEnum):
    """Subscription tier controlling feature access and rate limits."""

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class DeploymentStatus(StrEnum):
    """Operational status of a tenant deployment."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class TenantRateLimits(BaseModel):
    """Org-level rate limit thresholds enforced across all users.

    Example:
        >>> limits = TenantRateLimits(rpm=500, tpm=50_000)
        >>> limits.rpm
        500
    """

    model_config = ConfigDict(frozen=True)

    rpm: int = Field(
        default=1000,
        ge=1,
        description="Maximum requests per minute across the entire tenant.",
    )
    tpm: int = Field(
        default=100_000,
        ge=1,
        description="Maximum tokens per minute across the entire tenant.",
    )
    concurrent_requests: int = Field(
        default=10,
        ge=1,
        description="Maximum concurrent in-flight requests at any moment.",
    )


class TenantConfig(BaseModel):
    """Org-level configuration loaded from the tenants table.

    Frozen — safe to share across concurrent requests that share the same tenant.
    Secrets (api_key) are NEVER stored here; only metadata that informs routing.

    Example:
        >>> tc = TenantConfig(
        ...     tenant_id=UUID("..."),
        ...     tenant_name="Acme Corp",
        ...     status=TenantStatus.ACTIVE,
        ...     tier=TenantTier.ENTERPRISE,
        ... )
        >>> tc.is_active
        True
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID = Field(description="Unique tenant identifier (UUID v4).")
    tenant_name: str = Field(description="Display name for this organisation.")
    tenant_slug: str = Field(description="URL-friendly unique identifier, e.g. 'acme-corp'.")
    status: TenantStatus = Field(description="Current lifecycle status.")
    tier: TenantTier = Field(
        default=TenantTier.FREE,
        description="Subscription tier.",
    )
    rate_limits: TenantRateLimits = Field(
        default_factory=TenantRateLimits,
        description="Org-wide rate limit thresholds.",
    )
    # WHY: None means all providers are allowed. A frozen set restricts to a
    # subset, enabling tenants to be locked to approved providers only.
    allowed_provider_names: frozenset[str] | None = Field(
        default=None,
        description="Whitelisted provider names. None = all providers allowed.",
    )

    @property
    def is_active(self) -> bool:
        """Return True when the tenant can accept new requests.

        Returns:
            True only when status is ACTIVE or TRIAL.
        """
        return self.status in {TenantStatus.ACTIVE, TenantStatus.TRIAL}

    def allows_provider(self, provider_name: str) -> bool:
        """Check whether a provider is permitted for this tenant.

        Args:
            provider_name: Lowercase provider identifier (e.g., 'openai').

        Returns:
            True if the tenant can use this provider.
        """
        if self.allowed_provider_names is None:
            return True
        return provider_name in self.allowed_provider_names


class DeploymentConfig(BaseModel):
    """Per-(tenant, deployment) runtime configuration sourced from PostgreSQL.

    This is the narrowest static settings level. It captures which provider +
    model a tenant has deployed, plus operational overrides. The actual API key
    is NOT stored here — only the secret_reference used to fetch it from
    SecretStore at provider build time.

    Example:
        >>> dc = DeploymentConfig(
        ...     deployment_id=UUID("..."),
        ...     tenant_id=UUID("..."),
        ...     deployment_key="gpt4-production",
        ...     provider_name="openai",
        ...     model_name="gpt-4o",
        ...     api_endpoint_url="https://api.openai.com/v1",
        ...     secret_reference="secret/acme/openai-key",
        ... )
    """

    model_config = ConfigDict(frozen=True)

    deployment_id: UUID
    tenant_id: UUID
    deployment_key: str = Field(
        description="URL-safe human identifier, e.g. 'gpt4-prod'.",
    )
    deployment_name: str = Field(description="Human display name.")
    status: DeploymentStatus = Field(default=DeploymentStatus.ACTIVE)

    # ── Provider + Model ──────────────────────────────────────────────────
    provider_name: str = Field(
        description="Must match a key in ProviderRegistry's static settings map.",
    )
    model_name: str = Field(
        description="Model name used in API calls (e.g., 'gpt-4o').",
    )

    # ── Endpoint + Credentials ────────────────────────────────────────────
    api_endpoint_url: str = Field(
        description="Fully resolved provider API base URL for this deployment.",
    )
    # WHY: Never store the plaintext API key in settings. Store only a reference
    # string pointing to the encrypted value in SecretStore.
    secret_reference: str = Field(
        description=(
            "Opaque key used to retrieve the decrypted API key from SecretStore "
            "(e.g., 'secret/acme-corp/openai-key'). Never the API key itself."
        ),
    )
    cloud_region: str | None = Field(
        default=None,
        description="Cloud region for region-specific endpoints (e.g., AWS Bedrock).",
    )

    # ── Request Overrides (override ProviderStaticConfig defaults) ────────
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Deployment-specific timeout. None = use provider default.",
    )
    max_retries: int | None = Field(
        default=None,
        ge=0,
        description="Deployment-specific retry count. None = use provider default.",
    )
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_max_tokens: int | None = Field(default=None, gt=0)

    # ── Extra Config (provider-specific JSONB from DB) ────────────────────
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Additional HTTP headers merged into every request.",
    )
    extra_config: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-specific options stored in the DB settings JSONB column.",
    )

    # ── Access Control ────────────────────────────────────────────────────
    is_default: bool = Field(
        default=False,
        description="Whether this is the tenant's default deployment for its provider.",
    )
    priority: int = Field(
        default=0,
        description="Higher = preferred in load balancing / fallback routing.",
    )

    @field_validator("provider_name")
    @classmethod
    def validate_provider_name_lowercase(cls, value: str) -> str:
        """Enforce lowercase provider names to prevent lookup mismatches.

        Args:
            value: Raw provider name.

        Returns:
            Lowercased provider name.

        Raises:
            ValueError: If the name is not already lowercase.
        """
        if value != value.lower():
            raise ValueError(
                f"provider_name must be lowercase, got {value!r}. Use {value.lower()!r} instead."
            )
        return value

    @property
    def is_active(self) -> bool:
        """True when this deployment can accept new requests.

        Returns:
            True only when status is ACTIVE.
        """
        return self.status == DeploymentStatus.ACTIVE


class UserEntitlementConfig(BaseModel):
    """Per-user personal LLM entitlement with an individual API key.

    Users may bring their own API keys for providers their tenant allows.
    Priority resolution: UserEntitlementConfig > DeploymentConfig (tenant shared).

    Example:
        >>> ue = UserEntitlementConfig(
        ...     entitlement_id=UUID("..."),
        ...     user_id=UUID("..."),
        ...     tenant_id=UUID("..."),
        ...     provider_name="openai",
        ...     model_name="gpt-4o",
        ...     api_endpoint_url="https://api.openai.com/v1",
        ...     secret_reference="secret/user/bob-openai-key",
        ... )
    """

    model_config = ConfigDict(frozen=True)

    entitlement_id: UUID
    user_id: UUID
    tenant_id: UUID
    entitlement_name: str = Field(description="Human label for this entitlement.")

    provider_name: str
    model_name: str
    api_endpoint_url: str
    # WHY: Same secret_reference pattern as DeploymentConfig — never plaintext keys.
    secret_reference: str = Field(
        description="SecretStore reference for this user's personal API key.",
    )

    cloud_provider: str | None = Field(default=None)
    cloud_region: str | None = Field(default=None)

    extra_config: dict[str, object] = Field(default_factory=dict)
    is_active: bool = Field(default=True)
