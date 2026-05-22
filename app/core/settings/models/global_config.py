"""
Global Configuration Models — System-wide defaults loaded from base.yaml.

These models represent the top of the configuration hierarchy. Every field
has a safe default so the service can start with zero configuration and be
progressively hardened for production.

Architecture:
-------------
    base.yaml  ──►  ConfigLoader  ──►  GlobalConfig  ──►  All Providers
                                             │
                               ┌─────────────┼──────────────┐
                               ▼             ▼              ▼
                        HTTPPoolConfig  RetryConfig   LoggingConfig

Step-by-step relation:
    1. Base and environment YAML files are merged by ``ConfigLoader``.
    2. Result is validated into ``GlobalConfig``.
    3. Infrastructure factories (HTTP clients, retries, logging) consume
       nested sections from this typed model.
    4. Values remain immutable for process lifetime to avoid config drift.

Dependencies:
    - pydantic >= 2.0 — frozen BaseModel for thread-safe immutability

Author: Shubham Singh
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoggingConfig(BaseModel):
    """Logging configuration — controls output format and verbosity.

    Example:
        >>> cfg = LoggingConfig(level="DEBUG", format="json")
        >>> cfg.level
        'DEBUG'
    """

    model_config = ConfigDict(frozen=True)

    level: str = Field(
        default="INFO",
        description="Log level: DEBUG | INFO | WARNING | ERROR | CRITICAL",
    )
    format: str = Field(
        default="json",
        description="Output format: json | text",
    )
    include_request_body: bool = Field(
        default=False,
        description="Whether to log raw prompt/completion bodies. Never enable in prod.",
    )

    @field_validator("level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Ensure only valid stdlib logging levels are accepted.

        Args:
            value: Raw level string from settings.

        Returns:
            Uppercased, validated level string.

        Raises:
            ValueError: If the level is not a recognised stdlib level.
        """
        valid_levels: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
        upper: str = value.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log level {value!r}. Must be one of: {sorted(valid_levels)}")
        return upper


class HTTPPoolConfig(BaseModel):
    """HTTP connection pool settings shared across all providers.

    These limits apply to the single shared httpx transport. Setting them
    too low starves concurrent requests; too high exhausts OS file descriptors.

    Example:
        >>> cfg = HTTPPoolConfig(max_connections=50)
        >>> cfg.max_connections
        50
    """

    model_config = ConfigDict(frozen=True)

    max_connections: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description="Total maximum open TCP connections.",
    )
    max_keepalive_connections: int = Field(
        default=20,
        ge=1,
        description="Maximum idle keep-alive connections held open.",
    )
    keepalive_expiry_seconds: int = Field(
        default=300,
        ge=1,
        description="Seconds before an idle keep-alive connection is closed.",
    )
    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for establishing a new TCP connection.",
    )
    read_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Timeout waiting for the first byte of a response.",
    )
    write_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for sending the full request body.",
    )


class RetryConfig(BaseModel):
    """Exponential-backoff retry policy applied at the provider layer.

    Only transient server-side errors are retried. Client errors (4xx, except
    429) are never retried — they indicate a logic bug.

    Example:
        >>> cfg = RetryConfig(max_attempts=5)
        >>> cfg.retryable_status_codes
        (429, 500, 502, 503, 504)
    """

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of total attempts (1 = no retries).",
    )
    backoff_multiplier: float = Field(
        default=1.0,
        gt=0,
        description="Multiplier for exponential backoff: delay = multiplier * 2^attempt.",
    )
    backoff_max_seconds: float = Field(
        default=8.0,
        gt=0,
        description="Upper bound on backoff delay in seconds.",
    )
    retryable_status_codes: tuple[int, ...] = Field(
        default=(429, 500, 502, 503, 504),
        description="HTTP status codes that trigger a retry.",
    )


class ServiceConfig(BaseModel):
    """Service identity metadata injected into every log record.

    Example:
        >>> cfg = ServiceConfig(name="llm-provider-service", environment="production")
        >>> cfg.environment
        'production'
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        default="llm-provider-service",
        description="Service name emitted in every structured log.",
    )
    version: str = Field(
        default="0.1.0",
        description="Semantic version of this service deployment.",
    )
    environment: str = Field(
        default="development",
        description="Deployment environment: development | staging | production",
    )


class GlobalConfig(BaseModel):
    """Root configuration model assembled from base.yaml + environment overlay.

    Passed as a dependency to HTTPClientFactory, ProviderRegistry, and logging.
    Frozen — never mutated after construction.

    Example:
        >>> cfg = GlobalConfig(
        ...     service=ServiceConfig(environment="production"),
        ...     http_pool=HTTPPoolConfig(max_connections=200),
        ...     retry=RetryConfig(max_attempts=5),
        ...     logging=LoggingConfig(level="WARNING"),
        ... )
        >>> cfg.service.environment
        'production'
    """

    model_config = ConfigDict(frozen=True)

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    http_pool: HTTPPoolConfig = Field(default_factory=HTTPPoolConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
