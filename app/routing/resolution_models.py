"""
Resolution Models
=================

Defines the internal request and result contracts used by the resolution
services package.

Architecture:
-------------
    callers
        │
        ├── ResolutionRequest
        └── RequestResolutionService
                │
                └── ResolvedExecutionContext

Dependencies:
    - app.schemas.enums — operation enum
    - app.core.settings.models.* — frozen tenant, deployment, provider, and model models

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING  # noqa: F401
from uuid import UUID  # noqa: TC003 — needed at runtime for Pydantic field resolution

from pydantic import BaseModel, ConfigDict, Field

# The following imports are used in Pydantic field annotations and MUST be
# available at runtime for Pydantic model building / validation.
from app.core.settings.models.model_config import LLMModelSpec  # noqa: TC001
from app.core.settings.models.provider_config import ProviderStaticConfig  # noqa: TC001
from app.core.settings.models.tenant_config import (  # noqa: TC001
    DeploymentConfig,
    TenantConfig,
    UserEntitlementConfig,
)
from app.schemas.enums import OperationType  # noqa: TC001


class ResolutionSource(StrEnum):
    """Identifies which routing layer won during resolution."""

    USER_ENTITLEMENT = "user_entitlement"
    TENANT_DEPLOYMENT = "tenant_deployment"


class CredentialScope(StrEnum):
    """Indicates which identity owns the credential reference."""

    USER = "user"
    TENANT = "tenant"


class ResolutionRequest(BaseModel):
    """Execution intent passed into the resolution layer."""

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
    """Immutable execution-ready result returned by the resolution orchestrator."""

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
