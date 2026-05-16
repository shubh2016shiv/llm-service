"""
Provider Static Configuration Models — Immutable, YAML-loaded provider metadata.

ProviderStaticConfig is the system-level definition of an LLM provider type
(OpenAI, Anthropic, Bedrock, etc.). It is the same for all tenants. Tenant-
specific overrides (API key, model, timeouts) live in DeploymentConfig, not here.

Architecture:
-------------
    config/providers/openai.yaml
          │
          ▼
    ConfigLoader.load_provider_config("openai")
          │
          ▼
    ProviderStaticConfig  ◄── shared by all (tenant, deployment) pairs
    ├── ProviderAuthConfig        auth strategy (Bearer, api-key header, SigV4)
    ├── ProviderEndpointConfig    base URL + operation paths
    └── Tuple[LLMModelSpec, ...]  per-model specs

Dependencies:
    - pydantic >= 2.0       — frozen BaseModel
    - .model_config         — LLMModelSpec, ModelCapability

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING  # noqa: F401

from pydantic import BaseModel, ConfigDict, Field, field_validator

# These are used in Pydantic field annotations and MUST be at runtime.
from app.core.settings.models.model_config import LLMModelSpec, ModelCapability  # noqa: TC001


class AuthMode(StrEnum):
    """Supported authentication strategies for external LLM providers.

    BEARER_TOKEN  — Authorization: Bearer <key>
    API_KEY_HEADER — Custom header, e.g. x-api-key: <key>
    AWS_SIGV4     — AWS Signature Version 4 (used by Bedrock)
    OAUTH         — OAuth 2.0 client credentials flow
    NONE          — No authentication (private/local endpoints)
    """

    BEARER_TOKEN = "bearer_token"
    API_KEY_HEADER = "api_key_header"
    AWS_SIGV4 = "aws_sigv4"
    OAUTH = "oauth"
    NONE = "none"


class ProviderType(StrEnum):
    """Transport layer used by a provider.

    REST_API — Standard HTTPS REST (OpenAI, Anthropic, VLLM, Azure)
    AWS_SDK  — boto3 / aioboto3 (Bedrock) — not httpx-based
    GRPC     — gRPC streaming (future use)
    """

    REST_API = "rest_api"
    AWS_SDK = "aws_sdk"
    GRPC = "grpc"


class ProviderAuthConfig(BaseModel):
    """Authentication strategy for a provider type.

    Immutable — defined in YAML, not editable per-tenant. The actual secret
    value (API key) is NEVER stored here; it lives in SecretStore and is
    injected at request time.

    Example:
        >>> auth = ProviderAuthConfig(
        ...     mode=AuthMode.BEARER_TOKEN,
        ...     header_name="Authorization",
        ...     header_prefix="Bearer",
        ... )
    """

    model_config = ConfigDict(frozen=True)

    mode: AuthMode = Field(
        description="Authentication strategy for this provider.",
    )
    header_name: str | None = Field(
        default=None,
        description="HTTP header name carrying the credential (REST providers only).",
    )
    header_prefix: str | None = Field(
        default=None,
        description="String prepended to the credential value (e.g., 'Bearer').",
    )
    aws_service_name: str | None = Field(
        default=None,
        description="AWS service name for SigV4 signing (e.g., 'bedrock-runtime').",
    )


class ProviderEndpointConfig(BaseModel):
    """Base URL and operation-specific path segments for a provider.

    Example:
        >>> ep = ProviderEndpointConfig(
        ...     base_url="https://api.openai.com/v1",
        ...     chat="/chat/completions",
        ...     embed="/embeddings",
        ... )
        >>> ep.base_url + ep.chat
        'https://api.openai.com/v1/chat/completions'
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(
        description="Root URL for the provider API, without trailing slash.",
    )
    base_url_template: str | None = Field(
        default=None,
        description=(
            "URL template with placeholders, e.g. "
            "'https://bedrock-runtime.{region}.amazonaws.com'. "
            "Used instead of base_url when the URL varies by region."
        ),
    )
    chat: str | None = Field(
        default=None,
        description="Path segment for the chat/completions operation.",
    )
    embed: str | None = Field(
        default=None,
        description="Path segment for the embeddings operation.",
    )
    rerank: str | None = Field(
        default=None,
        description="Path segment for the reranking operation.",
    )
    health: str | None = Field(
        default=None,
        description="Path segment for the health-check probe.",
    )

    def resolve_base_url(self, region: str | None = None) -> str:
        """Resolve the effective base URL, expanding template placeholders.

        Args:
            region: Cloud region string to substitute into base_url_template.

        Returns:
            Fully resolved base URL string.

        Raises:
            ValueError: If base_url_template is set but no region is supplied.

        Example:
            >>> ep.resolve_base_url(region="us-east-1")
            'https://bedrock-runtime.us-east-1.amazonaws.com'
        """
        if self.base_url_template:
            if not region:
                raise ValueError("base_url_template requires a region, but none was provided.")
            return self.base_url_template.format(region=region)
        return self.base_url


class ProviderStaticConfig(BaseModel):
    """System-level, immutable definition of an LLM provider type.

    Loaded from config/providers/<name>.yaml at startup. Shared by all tenants
    that use this provider — never duplicated per tenant. The provider's Python
    class is referenced by implementation_class and dynamically imported by the
    ProviderRegistry.

    Example:
        >>> static = ProviderStaticConfig(
        ...     provider_name="openai",
        ...     provider_type=ProviderType.REST_API,
        ...     implementation_class="app.providers.openai_provider.OpenAIProvider",
        ...     auth=ProviderAuthConfig(mode=AuthMode.BEARER_TOKEN, ...),
        ...     endpoints=ProviderEndpointConfig(base_url="https://api.openai.com/v1", ...),
        ...     capabilities=frozenset({ModelCapability.CHAT, ModelCapability.EMBED}),
        ...     models=(gpt4o_spec, gpt4o_mini_spec),
        ... )
        >>> static.get_model_spec("gpt-4o")
        LLMModelSpec(name='gpt-4o', ...)
    """

    model_config = ConfigDict(frozen=True)

    provider_name: str = Field(
        description="Canonical lowercase provider identifier (e.g., 'openai').",
    )
    provider_type: ProviderType = Field(
        description="Transport layer used by this provider.",
    )
    implementation_class: str = Field(
        description=(
            "Fully-qualified Python class path for the concrete provider "
            "(e.g., 'app.providers.openai_provider.OpenAIProvider')."
        ),
    )

    auth: ProviderAuthConfig = Field(
        description="Authentication strategy for this provider.",
    )
    endpoints: ProviderEndpointConfig = Field(
        description="Base URL and per-operation path segments.",
    )

    capabilities: frozenset[ModelCapability] = Field(
        description="Operations supported by this provider (union of all model capabilities).",
    )

    # ── Default Request Settings ──────────────────────────────────────────
    default_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Provider-level default request timeout. Overridable per deployment.",
    )
    default_max_retries: int = Field(
        default=3,
        ge=0,
        description="Provider-level default retry count.",
    )
    default_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Provider-level default temperature.",
    )

    # ── Model Catalog ─────────────────────────────────────────────────────
    models: tuple[LLMModelSpec, ...] = Field(
        default=(),
        description="All model variants available via this provider.",
    )

    # ── Extra Provider-Specific Defaults ─────────────────────────────────
    extra_default_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Static headers always added to every request (e.g., API version).",
    )

    @field_validator("provider_name")
    @classmethod
    def validate_provider_name_lowercase(cls, value: str) -> str:
        """Enforce lowercase-only provider names to prevent key mismatches.

        Args:
            value: Raw provider name string from YAML.

        Returns:
            Lowercase provider name.

        Raises:
            ValueError: If the name contains uppercase characters.
        """
        if value != value.lower():
            raise ValueError(
                f"provider_name must be lowercase, got: {value!r}. Use {value.lower()!r} instead."
            )
        return value

    def get_model_spec(self, model_name: str) -> LLMModelSpec | None:
        """Look up a model spec by its name.

        Args:
            model_name: Model name as used in API requests (e.g., 'gpt-4o').

        Returns:
            LLMModelSpec for the named model, or None if not found.

        Example:
            >>> static.get_model_spec("nonexistent-model")
            None
        """
        for spec in self.models:
            if spec.name == model_name:
                return spec
        return None

    def supports_operation(self, capability: ModelCapability) -> bool:
        """Check whether this provider supports a given operation at all.

        Args:
            capability: The capability to check.

        Returns:
            True if at least one model in this provider supports the capability.
        """
        return capability in self.capabilities
