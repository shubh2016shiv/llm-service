"""Encryption, secret-backend selection, and JWT configuration.

Architecture:
    environment variables -> SecurityConfig -> auth and encryption consumers
"""

from __future__ import annotations

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
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm.")
    jwt_access_token_expire_hours: int = Field(
        default=24,
        ge=1,
        description="Access-token lifetime in hours.",
    )
    jwt_refresh_token_expire_days: int = Field(
        default=7,
        ge=1,
        description="Refresh-token lifetime in days.",
    )
    jwt_refresh_enabled: bool = Field(
        default=False,
        description="Whether refresh-token issuance and exchange are enabled.",
    )

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
