"""Encryption, secret-backend selection, and JWT configuration.

Architecture:
    environment variables -> SecurityConfig -> auth and encryption consumers
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class SecurityConfig(BaseModel):
    """Define security-sensitive runtime configuration.

    Algorithm: load secrets as masked values, validate the selected secret
    backend, then supply credentials only to trusted adapters.
    """

    encryption_master_key: SecretStr = Field(
        description="Base64-encoded master key used for AES-GCM key derivation.",
    )
    secret_backend: str = Field(
        default="environment",
        description="Secret backend: environment, vault, or aes_gcm.",
    )
    jwt_secret_key: SecretStr = Field(description="Secret key used to sign and verify JWTs.")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field(
        default="HS256",
        description="Allowed symmetric JWT verification algorithm.",
    )
    jwt_issuer: str = Field(
        default="llm-identity-service",
        min_length=1,
        description="Exact issuer claim accepted from the trusted identity service.",
    )
    jwt_audience: str = Field(
        default="llm-provider-service",
        min_length=1,
        description="Exact audience claim required for tokens sent to this API.",
    )
    jwt_clock_skew_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        description="Small expiry/not-before tolerance for clock differences between services.",
    )
    jwt_max_token_age_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Longest access-token lifetime this resource server will trust.",
    )

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        """Reject weak HMAC keys before the application begins accepting traffic."""
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("jwt_secret_key must contain at least 32 UTF-8 bytes")
        return value

    @field_validator("secret_backend")
    @classmethod
    def validate_secret_backend(cls, value: str) -> str:
        """Normalize and validate the configured secret backend name."""
        allowed = {"environment", "vault", "aes_gcm"}
        normalized_value = value.lower()
        if normalized_value not in allowed:
            raise ValueError(
                f"secret_backend {value!r} is not valid. Must be one of: {sorted(allowed)}"
            )
        return normalized_value
