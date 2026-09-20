"""Database and cache connection configuration.

Architecture:
    environment variables -> infrastructure config -> adapters
"""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, model_validator


class DatabaseConfig(BaseModel):
    """Define PostgreSQL connection and pool-capacity settings.

    Algorithm: validate safe pool bounds before the database session manager
    constructs its engine.
    """

    database_url: SecretStr = Field(
        description="Async PostgreSQL connection string.",
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
    database_pool_recycle_seconds: int = Field(
        default=1800,
        ge=1,
        description="Maximum connection age before SQLAlchemy recycles it.",
    )
    database_pool_timeout_seconds: int = Field(
        default=30,
        ge=1,
        description="Maximum wait for a pooled database connection.",
    )
    database_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Maximum time allowed to establish a PostgreSQL connection.",
    )
    database_statement_timeout_ms: int = Field(
        default=60_000,
        ge=1,
        description="PostgreSQL server-side timeout applied to every statement.",
    )


class CacheConfig(BaseModel):
    """Define Redis connectivity and inference-authorization cache settings.

    Algorithm: validate bounded cache capacity and TTL values before adapters
    use them for shared cache connections and authorization grants.
    """

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )
    redis_password: SecretStr | None = Field(
        default=None,
        description="Redis AUTH password, if the deployment requires one.",
    )
    redis_max_connections: int = Field(
        default=50,
        ge=1,
        description="Maximum Redis connection pool size.",
    )
    inference_authorization_cache_ttl_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="TTL for successful inference authorization cache entries.",
    )
    redis_socket_connect_timeout_seconds: int = Field(
        default=5,
        ge=1,
        description="Socket connect timeout applied to every Redis connection attempt.",
    )
    redis_socket_timeout_seconds: int = Field(
        default=5,
        ge=1,
        description="Maximum time a Redis command may wait for a response.",
    )
    redis_health_check_interval_seconds: int = Field(
        default=30,
        ge=1,
        description="Interval redis-py uses to verify pooled connections are alive.",
    )
    redis_initial_retry_delay_seconds: float = Field(
        default=1.0,
        gt=0,
        description="Delay before the first reconnect attempt after Redis becomes unreachable.",
    )
    redis_max_retry_delay_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Ceiling for the doubling reconnect and pub/sub resubscribe delay.",
    )

    @model_validator(mode="after")
    def validate_max_retry_delay_meets_initial(self) -> CacheConfig:
        """Ensure the retry ceiling never sits below the starting delay.

        Raises:
            ValueError: If redis_max_retry_delay_seconds is less than
                redis_initial_retry_delay_seconds.
        """
        if self.redis_max_retry_delay_seconds < self.redis_initial_retry_delay_seconds:
            raise ValueError(
                "redis_max_retry_delay_seconds "
                f"({self.redis_max_retry_delay_seconds}) must be >= "
                f"redis_initial_retry_delay_seconds ({self.redis_initial_retry_delay_seconds})."
            )
        return self


class TokenManagerConfig(BaseModel):
    """Define the internal token-manager HTTP integration."""

    token_manager_base_url: str = Field(
        default="http://llm_tm_api:8000",
        min_length=1,
        description="Internal base URL of the token-manager API.",
    )
    token_manager_service_id: str = Field(
        default="llm-services",
        min_length=1,
        description="Caller identity used for token-manager rate-limit buckets.",
    )
    token_manager_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    token_manager_read_timeout_seconds: float = Field(default=5.0, gt=0)
    token_manager_write_timeout_seconds: float = Field(default=5.0, gt=0)
    token_manager_pool_timeout_seconds: float = Field(default=2.0, gt=0)


class StreamingConfig(BaseModel):
    """Bound resource usage for long-lived SSE connections per API worker."""

    stream_max_concurrent_per_worker: int = Field(
        default=20,
        ge=1,
        le=10_000,
        description="Fail-fast concurrent SSE limit for each application worker.",
    )
    stream_heartbeat_interval_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="Maximum quiet period before an SSE heartbeat comment.",
    )
    stream_capacity_retry_after_seconds: int = Field(
        default=1,
        ge=1,
        le=60,
        description="Retry-After hint when one worker reaches stream capacity.",
    )
    stream_cleanup_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Bound provider iterator cleanup during disconnect handling.",
    )
