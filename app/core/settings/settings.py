"""Application-settings composition root.

Architecture:
    .env / environment variables
                 |
                 v
    ApplicationSettings (BaseSettings composition root)
       |          |          |          |          |
       v          v          v          v          v
    environment database    cache    security observability models

This module deliberately contains no field definitions. The focused Pydantic
models in ``models/`` own validation and documentation; this composition root
adds only rules that depend on more than one settings concern.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings.models.auth_session_config import AuthSessionConfig
from app.core.settings.models.environment_config import EnvironmentConfig
from app.core.settings.models.infrastructure_config import (
    CacheConfig,
    DatabaseConfig,
    ProviderRuntimeConfig,
    StreamingConfig,
    TokenManagerConfig,
)
from app.core.settings.models.observability_config import ObservabilityConfig
from app.core.settings.models.security_config import SecurityConfig
from app.core.settings.models.vault_config import VaultConfig


# Pyright compares BaseModel.model_config with BaseSettings.model_config even
# though Pydantic supports this field-mixin composition at runtime.
# pyright: ignore[reportIncompatibleVariableOverride]
class ApplicationSettings(  # pyright: ignore[reportIncompatibleVariableOverride]
    EnvironmentConfig,
    DatabaseConfig,
    CacheConfig,
    TokenManagerConfig,
    StreamingConfig,
    ProviderRuntimeConfig,
    SecurityConfig,
    AuthSessionConfig,
    VaultConfig,
    ObservabilityConfig,
    BaseSettings,
):
    """Compose all environment-backed configuration contracts.

    Algorithm:
        1. Read environment variables and the optional ``.env`` file once.
        2. Validate each field through its concern-specific settings model.
        3. Expose one flat, typed configuration object to application callers.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_issued_token_lifetime(self) -> ApplicationSettings:
        """Refuse to mint tokens this service's own validator would reject.

        ``access_token_ttl_seconds`` (issuance) and ``jwt_max_token_age_seconds``
        (verification) are set independently and live in different settings
        models. If issuance outgrows verification, every sign-in appears to
        succeed and every subsequent request fails with an opaque 401 — a
        failure that costs hours to trace back to configuration.
        """
        if self.access_token_ttl_seconds > self.jwt_max_token_age_seconds:
            raise ValueError(
                f"access_token_ttl_seconds ({self.access_token_ttl_seconds}) cannot exceed "
                f"jwt_max_token_age_seconds ({self.jwt_max_token_age_seconds}); this service "
                "would issue tokens its own validator rejects"
            )
        return self

    @model_validator(mode="after")
    def validate_production_safety(self) -> ApplicationSettings:
        """Reject development-only values when the process declares production."""
        if self.app_environment != "production":
            return self
        # The guest door skips authentication entirely, so a misconfigured
        # production deploy would publish an unauthenticated superuser. Failing
        # the launch is the only response that cannot be missed in a log.
        if self.guest_superuser_enabled:
            raise ValueError(
                "production cannot enable guest_superuser_enabled: it grants "
                "unauthenticated superuser access"
            )
        local_origins = {
            origin
            for origin in self.get_cors_allowed_origins()
            if "localhost" in origin or "127.0.0.1" in origin
        }
        if local_origins:
            raise ValueError(
                "production cors_allowed_origins cannot contain local origins: "
                f"{sorted(local_origins)}"
            )
        if self.jwt_secret_key.get_secret_value().startswith("change-me"):
            raise ValueError("production jwt_secret_key cannot use the example placeholder")
        if (
            self.jwt_secret_key.get_secret_value()
            == "local-jwt-signing-key-replace-outside-development"
        ):
            raise ValueError("production jwt_secret_key cannot use the compose development default")
        if (
            self.encryption_master_key.get_secret_value()
            == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        ):
            raise ValueError(
                "production encryption_master_key cannot use the compose development default"
            )
        return self


@lru_cache(maxsize=1)
def get_application_settings() -> ApplicationSettings:
    """Return the singleton environment-backed application configuration.

    Algorithm: construct the composed Pydantic settings model on first use and
    reuse it for the process lifetime, unless tests explicitly clear the cache.
    """
    # BaseSettings loads required fields from the environment at runtime.
    # Pyright only sees the composed BaseModel constructor and incorrectly
    # requires callers to pass those environment fields explicitly.
    return ApplicationSettings()  # pyright: ignore[reportCallIssue]
