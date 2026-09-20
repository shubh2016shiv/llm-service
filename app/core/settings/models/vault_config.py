"""HashiCorp Vault connection and token-lifecycle configuration."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, SecretStr, model_validator


class VaultConfig(BaseModel):
    """Define how the service connects to Vault and renews authentication.

    Algorithm:
        1. Load Vault connectivity and userpass credentials from the environment.
        2. Validate that token refresh occurs strictly within the lease lifetime.
        3. Pass the validated values to the Vault credential adapter at startup.
    """

    vault_addr: str = Field(
        default="http://localhost:8200",
        description="Vault server address.",
    )
    vault_username: str | None = Field(
        default=None,
        description="Vault userpass username for the service account.",
    )
    vault_password: SecretStr | None = Field(
        default=None,
        description="Vault userpass password for the service account.",
    )
    vault_admin_username: str | None = Field(
        default=None,
        description=(
            "Vault userpass username for the write-capable identity used only by the "
            "management API to store a captured credential. Deliberately distinct from "
            "vault_username: the read identity used on the inference hot path never "
            "gains write capability."
        ),
    )
    vault_admin_password: SecretStr | None = Field(
        default=None,
        description="Vault userpass password for the write-capable identity.",
    )
    vault_mount_path: str = Field(
        default="secret",
        description="Vault KV v2 mount path.",
    )
    vault_kv_prefix: str = Field(
        default="llm-provider-service",
        description="Path prefix within the Vault KV mount.",
    )
    vault_token_refresh_after_lease_fraction: float = Field(
        default=0.9,
        gt=0.0,
        lt=1.0,
        description=("Refresh a cached Vault token after this fraction of its lease has elapsed."),
    )
    vault_connect_timeout_seconds: float = Field(
        default=5.0, gt=0, description="Maximum seconds to establish a Vault connection."
    )
    vault_read_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Maximum seconds waiting for Vault response data."
    )
    vault_write_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Maximum seconds sending a request to Vault."
    )
    vault_pool_timeout_seconds: float = Field(
        default=2.0, gt=0, description="Maximum wait for an available Vault connection."
    )
    vault_request_max_attempts: int = Field(
        default=3, ge=1, le=10, description="Total attempts for transient Vault failures."
    )
    vault_retry_base_delay_seconds: float = Field(
        default=0.25, gt=0, description="Initial exponential retry-delay cap."
    )
    vault_retry_max_delay_seconds: float = Field(
        default=2.0, gt=0, description="Maximum full-jitter retry-delay cap."
    )

    @model_validator(mode="after")
    def validate_retry_window(self) -> Self:
        """Reject a retry policy whose initial cap exceeds its maximum cap."""
        if self.vault_retry_base_delay_seconds > self.vault_retry_max_delay_seconds:
            raise ValueError(
                "vault_retry_base_delay_seconds cannot exceed vault_retry_max_delay_seconds"
            )
        return self
