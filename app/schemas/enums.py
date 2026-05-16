"""
app/schemas/enums.py — Canonical enumerations for the LLM gateway.

These enums are the single source of truth for provider types, operations,
and authentication modes. They are used across config models, provider
implementations, and API validation.
"""

from enum import StrEnum


class ProviderType(StrEnum):
    """How a provider communicates — determines transport and auth strategy.

    Values are stored in the DB `providers.provider_type` column and in
    YAML static config (`provider_type` field).
    """

    REST_API = "rest_api"  # Standard HTTPS REST (OpenAI, Anthropic, vLLM, Azure)
    AWS_SDK = "aws_sdk"  # boto3 / aioboto3 (Bedrock)
    GRPC = "grpc"  # Reserved for future gRPC-based providers


class OperationType(StrEnum):
    """LLM operations the gateway can dispatch to a provider.

    Used in structured logging (`operation` field) and in capability checks
    (ProviderStaticConfig.capabilities).
    """

    CHAT = "chat"  # Chat completions (generate + stream_generate)
    EMBED = "embed"  # Text embeddings
    RERANK = "rerank"  # Document re-ranking
    HEALTH = "health"  # Provider health check


class AuthMode(StrEnum):
    """Authentication strategy for a provider.

    Stored in YAML static config (`auth.mode`). Determines how the provider's
    _build_auth_headers() or SDK credential chain behaves.
    """

    BEARER_TOKEN = "bearer_token"  # Authorization: Bearer <key>
    API_KEY_HEADER = "api_key_header"  # Custom header (x-api-key, api-key, etc.)
    AWS_SIGV4 = "aws_sigv4"  # AWS Signature V4 (Bedrock)
    OAUTH = "oauth"  # Reserved for OAuth2 client credentials
