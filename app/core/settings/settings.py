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

from app.core.settings.models.environment_config import EnvironmentConfig
from app.core.settings.models.infrastructure_config import (
    CacheConfig,
    DatabaseConfig,
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
    SecurityConfig,
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
    def validate_production_safety(self) -> ApplicationSettings:
        """Reject development-only values when the process declares production."""
        if self.app_environment != "production":
            return self
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
        return self


@lru_cache(maxsize=1)
def get_application_settings() -> ApplicationSettings:
    """Return the singleton environment-backed application configuration.

    Algorithm: construct the composed Pydantic settings model on first use and
    reuse it for the process lifetime, unless tests explicitly clear the cache.
    """
    return ApplicationSettings()
