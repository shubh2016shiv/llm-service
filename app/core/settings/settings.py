"""
Application Settings — Secrets and infrastructure URLs from environment variables.

This is the ONLY place where os.environ / .env values are read. All other
settings (pool sizes, timeouts, provider defaults) comes from YAML via ConfigLoader.

Why separate from YAML?
    YAML is version-controlled and safe to commit.
    Secrets and DB URLs are never committed — they come from the environment.

Architecture:
-------------
    .env / environment variables
          │
          ▼
    ApplicationSettings   (pydantic-settings, loaded once at startup)
          │
          ├──► database_url         → DB pool / SQLAlchemy engine
          ├──► redis_url            → Redis client
          ├──► encryption_master_key → SecretStore (AES-GCM key derivation)
          └──► app_environment      → selects config/environments/<env>.yaml overlay

Dependencies:
    - pydantic-settings >= 2.0

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApplicationSettings(BaseSettings):
    """Centralised loader for environment-sourced settings.

    All values come from environment variables or a .env file. Never scatter
    os.environ.get() calls throughout the codebase — import this class instead.

    Sensitive fields use SecretStr so pydantic masks their value in repr/logs.

    Example:
        >>> settings = get_application_settings()
        >>> settings.app_environment
        'development'
        >>> str(settings.encryption_master_key)   # masked
        '**********'
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # WHY: extra="ignore" prevents startup failure when the environment
        # contains unrelated variables (e.g., PATH, HOME).
        extra="ignore",
    )

    # ── Service Identity ──────────────────────────────────────────────────
    app_environment: str = Field(
        default="development",
        description=(
            "Active environment: development | staging | production. "
            "Selects config/environments/<env>.yaml overlay."
        ),
    )
    service_name: str = Field(
        default="llm-provider-service",
        description="Injected into every structured log record.",
    )
    service_version: str = Field(
        default="0.1.0",
        description="Semantic version emitted in logs and health endpoints.",
    )

    # ── Database ──────────────────────────────────────────────────────────
    database_url: SecretStr = Field(
        description=(
            "Async PostgreSQL connection string. "
            "Example: postgresql+asyncpg://user:pass@host:5432/dbname"
        ),
    )
    database_pool_size: int = Field(
        default=10,
        ge=1,
        le=200,
        description="SQLAlchemy async engine pool size.",
    )
    database_max_overflow: int = Field(
        default=20,
        ge=0,
        description="Extra connections beyond pool_size allowed to overflow.",
    )

    # ── Redis ─────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL (plain, no auth — use redis_password for auth).",
    )
    redis_password: SecretStr | None = Field(
        default=None,
        description="Redis AUTH password. None for unauthenticated local Redis.",
    )
    redis_max_connections: int = Field(
        default=50,
        ge=1,
        description="Maximum Redis connection pool size.",
    )

    # ── Encryption ────────────────────────────────────────────────────────
    # WHY: We derive per-tenant keys via HKDF(master_key + tenant_id) so that
    # compromising one tenant's derived key does not expose other tenants.
    encryption_master_key: SecretStr = Field(
        description=(
            "Base64-encoded 32-byte master key used for AES-GCM key derivation. "
            "Generate with: python -c \"import secrets,base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        ),
    )

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Overrides YAML logging.level. DEBUG | INFO | WARNING | ERROR",
    )

    # ── Config Paths ──────────────────────────────────────────────────────
    config_dir: str = Field(
        default="config",
        description="Filesystem path to the YAML configuration root directory.",
    )

    @field_validator("app_environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        """Enforce known environment names to prevent silent misconfiguration.

        Args:
            value: Raw environment string from env var.

        Returns:
            Lowercased, validated environment name.

        Raises:
            ValueError: If the environment is not in the allowed set.
        """
        allowed = {"development", "staging", "production", "test"}
        lower = value.lower()
        if lower not in allowed:
            raise ValueError(
                f"app_environment {value!r} is not valid. "
                f"Must be one of: {sorted(allowed)}"
            )
        return lower

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalise and validate the log level string.

        Args:
            value: Raw log level from environment.

        Returns:
            Uppercased, validated log level.

        Raises:
            ValueError: If not a recognised stdlib logging level.
        """
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in valid:
            raise ValueError(f"log_level {value!r} is invalid. Must be one of: {sorted(valid)}")
        return upper


@lru_cache(maxsize=1)
def get_application_settings() -> ApplicationSettings:
    """Return the singleton ApplicationSettings instance.

    Cached via lru_cache — the environment is read exactly once per process.
    Call this function wherever settings are needed instead of instantiating
    ApplicationSettings directly.

    Returns:
        The singleton ApplicationSettings instance.

    Example:
        >>> settings = get_application_settings()
        >>> settings.app_environment
        'development'
    """
    return ApplicationSettings()  # type: ignore[call-arg]
