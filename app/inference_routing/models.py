"""
Inference Routing Models
========================

Typed request and response models for the routing pipeline.

Key objects:
    - ResolutionRequest: routing intent from API layer.
    - ResolvedExecutionContext: immutable result consumed by inference service.

Enterprise Pattern: Immutable Contract Pattern
    A frozen context prevents accidental mutation across service boundaries.

Why these models are central:
    - ``ResolutionRequest`` is the only input contract for routing decisions.
    - ``ResolvedExecutionContext`` is the only output contract for execution.
    This boundary keeps routing deterministic and reduces hidden coupling.

Step-by-step data flow:
    1. API/auth builds ``ResolutionRequest`` from headers/token context.
    2. Pipeline resolves tenant/deployment/entitlement/provider/model.
    3. Factory creates ``ResolvedExecutionContext``.
    4. Inference service executes provider call using resolved fields.

Author: Shubham Singh
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Pydantic field annotations — must be available at runtime for model building.
from app.core.settings.models.model_config import LLMModelSpec
from app.core.settings.models.provider_config import ProviderStaticConfig
from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    TenantConfig,
    UserEntitlementConfig,
)
from app.schemas.enums import OperationType


class ResolutionSource(StrEnum):
    """Identify which route source won precedence during resolution.

    Useful for debugging and analytics (for example, measuring how often
    user overrides are used versus tenant default deployments).
    """

    USER_ENTITLEMENT = "user_entitlement"
    TENANT_DEPLOYMENT = "tenant_deployment"


class CredentialScope(StrEnum):
    """Indicate ownership of the selected credential reference.

    USER means user-scoped entitlement credential.
    TENANT means tenant deployment credential.
    """

    USER = "user"
    TENANT = "tenant"


class ResolutionRequest(BaseModel):
    """Immutable request intent consumed by the orchestration pipeline.

    This model intentionally includes only routing-relevant fields and avoids
    provider payload data so routing remains lightweight and policy-focused.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    user_id: UUID
    deployment_key: str = Field(
        min_length=1,
        description="Tenant-scoped deployment key used as the primary routing hint.",
    )
    operation: OperationType = Field(
        description="Requested LLM capability, such as chat or embed.",
    )
    # Set by the auth layer so the entitlement resolver pins to the record that
    # was already verified, preventing independent re-resolution from picking a
    # different candidate or raising AmbiguousUserEntitlementError.
    pre_authorized_entitlement_id: UUID | None = Field(
        default=None,
        description="Entitlement ID already verified by the authorization layer.",
    )
    requested_model_name: str | None = Field(
        default=None,
        description="Optional secondary model hint for user-entitlement matching.",
    )
    token_request_id: str | None = Field(
        default=None,
        description="Optional token-allocation identifier for correlation only.",
    )
    trace_id: str | None = Field(
        default=None,
        description="Optional distributed-trace identifier.",
    )


class ResolvedExecutionContext(BaseModel):
    """Immutable routing result passed into inference execution services.

    What this contains:
        - Route source and resolved config objects.
        - Concrete provider/model/endpoint/credential reference fields.
        - Effective runtime parameters (timeouts/retries/tokens).
        - Stable identifiers for quota and cache correlation.
    """

    model_config = ConfigDict(frozen=True)

    resolution_source: ResolutionSource
    tenant_config: TenantConfig
    deployment_config: DeploymentConfig | None = None
    user_entitlement_config: UserEntitlementConfig | None = None
    provider_static_config: ProviderStaticConfig
    model_spec: LLMModelSpec

    provider_name: str
    model_name: str
    api_endpoint_url: str
    cloud_region: str | None = None
    secret_reference: str
    credential_scope: CredentialScope

    effective_timeout_seconds: float
    effective_max_retries: int
    effective_temperature: float
    effective_max_tokens: int

    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Deployment-level HTTP headers to merge into every outbound request.",
    )
    extra_config: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-specific options (e.g. azure_deployment_name, aws_region).",
    )
    quota_key: str = Field(
        description=(
            "Stable identifier used for token-quota tracking. "
            "deployment_key for Path B, entitlement_id (str) for Path A."
        ),
    )
    route_fingerprint: str = Field(
        description="Stable digest of the resolved route and credential reference.",
    )

