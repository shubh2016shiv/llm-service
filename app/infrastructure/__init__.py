"""
app/infrastructure — Infrastructure adapters for the LLM gateway.

These adapters encapsulate external I/O concerns such as HTTP connection
pooling and Redis caching/pub-sub.

Package Structure
-----------------
    infrastructure/
    ├── http_client_factory.py   ← HTTPClientFactory (pooled httpx + aioboto3)
    ├── cache.py                 ← RedisCache (async Redis with pub/sub)
    └── circuit_breaker.py       ← Provider circuit breakers backed by Redis

Usage
-----
    from app.infrastructure import HTTPClientFactory, RedisCache

Configuration Loading
---------------------
    Import ConfigLoader from app.core.settings:

        from app.core.settings import ConfigLoader
"""

from app.infrastructure.cache import RedisCache
from app.infrastructure.http_client_factory import HTTPClientFactory

__all__ = [
    "HTTPClientFactory",
    "RedisCache",
]
