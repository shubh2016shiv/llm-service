"""Tests for adapter URL validation at the environment boundary."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.settings.models.infrastructure_config import (
    CacheConfig,
    DatabaseConfig,
    TokenManagerConfig,
)


def test_database_url_requires_asyncpg_driver() -> None:
    """The async session adapter cannot accidentally receive a synchronous DSN."""
    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        DatabaseConfig(database_url=SecretStr("postgresql://user:pass@db/service"))


def test_redis_url_rejects_unrelated_scheme() -> None:
    """Cache startup rejects addresses redis-py cannot consume."""
    with pytest.raises(ValueError, match="redis"):
        CacheConfig(redis_url="https://cache.example.com")


def test_token_manager_url_is_normalized_without_trailing_slash() -> None:
    """Client path joining starts from one canonical base URL."""
    config = TokenManagerConfig(token_manager_base_url="https://tokens.example.com/")

    assert config.token_manager_base_url == "https://tokens.example.com"
