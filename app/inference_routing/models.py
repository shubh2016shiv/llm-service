"""
The request and answer shapes
==============================

What this file is for
---------------------
Two frozen shapes, one for each end of the routing conversation:

    ResolutionRequest  — what goes IN: everything the resolver needs to
                         validate the exact authorization decision (tenant,
                         user, deployment, entitlement, and operation).
    ResolvedRoute      — what comes OUT: everything downstream execution
                         needs, with every "which setting wins?" question
                         already answered (effective timeout, temperature,
                         token limit, headers...).

Both forbid unknown fields and prevent attribute reassignment, making contract
drift fail loudly at construction time.
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# UUID = the globally unique id type used by every record in this project.
from uuid import UUID

# pydantic BaseModel = validates every field as the object is built.
# ConfigDict(frozen=True) = the object cannot be changed afterwards.
# Field = attach rules (lengths, defaults, descriptions) to a field.
from pydantic import BaseModel, ConfigDict, Field

# The provider's catalog entry (its default timeout etc.) — carried in
# the route so execution never has to re-read the catalog.
from app.core.settings.models.provider_config import ProviderStaticConfig

# The operation vocabulary (chat / embed / rerank / ...).
from app.schemas.enums import OperationType


class ResolutionRequest(BaseModel):
    """The question the resolver is asked.

    Built AFTER authentication and authorization: everything in here is
    already trusted. The resolver verifies that exact grant still exists and
    enriches it with current provider/model execution settings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID  # which customer is asking
    user_id: UUID  # which user is asking
    deployment_key: str = Field(min_length=1)  # which route they want
    operation: OperationType  # chat, embed, rerank, ...
    entitlement_id: UUID = Field(
        description="Exact entitlement already verified by authorization.",
    )


class ResolvedRoute(BaseModel):
    """The complete answer: every fact execution needs, nothing more.

    The "effective" fields mean request execution receives one resolved value
    and never has to re-derive provider/model defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID  # who this route belongs to
    deployment_key: str  # which route key it answers
    provider_static_config: ProviderStaticConfig  # the provider's catalog entry
    provider_name: str  # e.g. "openai"
    model_name: str  # e.g. "gpt-4o"
    api_endpoint_url: str  # where the HTTP call goes
    cloud_region: str | None = None  # region-specific endpoints (e.g. Bedrock)
    secret_reference: str  # WHERE the API key lives (never the key itself)
    effective_timeout_seconds: float  # the chosen timeout
    effective_temperature: float  # the chosen creativity knob
    effective_max_tokens: int  # the chosen answer-length limit
    extra_headers: dict[str, str] = Field(default_factory=dict)  # extra HTTP headers
    extra_config: dict[str, object] = Field(default_factory=dict)  # provider-specific options
    quota_key: str  # what the usage meter counts against
    route_fingerprint: str  # a fixed identity of this exact route
