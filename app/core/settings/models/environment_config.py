"""Environment and service-identity configuration.

Architecture:
    environment variables -> EnvironmentConfig -> ApplicationSettings

This model defines non-secret process identity and filesystem configuration.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EnvironmentConfig(BaseModel):
    """Define environment, service identity, and configuration-path settings.

    Algorithm: normalize the environment name, reject unknown deployment
    targets, and expose stable metadata to the application composition root.
    """

    app_environment: str = Field(
        default="development",
        description="Active environment: development, staging, production, or test.",
    )
    service_name: str = Field(
        default="llm-provider-service",
        description="Service name included in structured log records.",
    )
    service_version: str = Field(
        default="0.1.0",
        description="Semantic version emitted in logs and health endpoints.",
    )
    config_dir: str = Field(
        default="config",
        description="Filesystem path to the YAML configuration root directory.",
    )
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated browser origins allowed to call the API.",
    )

    @field_validator("app_environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        """Normalize and validate the selected deployment environment."""
        allowed = {"development", "staging", "production", "test"}
        normalized_value = value.lower()
        if normalized_value not in allowed:
            raise ValueError(
                f"app_environment {value!r} is not valid. Must be one of: {sorted(allowed)}"
            )
        return normalized_value

    def get_cors_allowed_origins(self) -> list[str]:
        """Return normalized dashboard origins for FastAPI CORS middleware."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]
