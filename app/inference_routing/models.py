"""
Inference Routing Models
========================

Typed request and response models for the routing pipeline.

Key objects:
    - ResolutionRequest: routing intent from API layer.
    - ResolvedExecutionContext: immutable result consumed by inference service.

Enterprise Pattern: Immutable Contract Pattern
    A frozen context prevents accidental mutation across service boundaries.

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
    """Identifies which routing layer won during resolution."""

    USER_ENTITLEMENT = "user_entitlement"
    TENANT_DEPLOYMENT = "tenant_deployment"


class CredentialScope(StrEnum):
    """Indicates which identity owns the credential reference."""

    USER = "user"
    TENANT = "tenant"


class ResolutionRequest(BaseModel):
    """Execution intent passed into the resolution pipeline."""

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
    """Immutable services-ready result returned by the resolution orchestrator."""

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

    route_fingerprint: str = Field(
        description="Stable digest of the resolved route and credential reference.",
    )

