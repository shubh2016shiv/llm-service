"""Environment and service-identity configuration.

Architecture:
    environment variables -> EnvironmentConfig -> ApplicationSettings

This model defines non-secret process identity and filesystem configuration.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.core.settings.url_validation import validate_browser_origin


class DeploymentEnvironment(StrEnum):
    """Deployment modes with distinct startup and observability behavior."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class EnvironmentConfig(BaseModel):
    """Define environment, service identity, and configuration-path settings.

    Algorithm: normalize the environment name, reject unknown deployment
    targets, and expose stable metadata to the application composition root.
    """

    app_environment: DeploymentEnvironment = Field(
        default=DeploymentEnvironment.DEVELOPMENT,
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
    config_dir: Path = Field(
        default=Path("config"),
        description="Filesystem path to the YAML configuration root directory.",
    )
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated browser origins allowed to call the API.",
    )
    api_max_request_body_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
        description="Maximum inbound HTTP request body size before parsing.",
    )

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        """Reject wildcard, malformed, and non-origin CORS entries at startup."""
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("cors_allowed_origins must contain at least one origin")
        if "*" in origins:
            raise ValueError("cors_allowed_origins cannot use '*' with authenticated APIs")
        return ",".join(validate_browser_origin(origin) for origin in origins)

    def get_cors_allowed_origins(self) -> list[str]:
        """Return normalized dashboard origins for FastAPI CORS middleware."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]
