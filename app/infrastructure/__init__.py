"""
Infrastructure Package
======================

Adapters for external systems and runtime platform concerns.

Scope:
    ``app.infrastructure`` contains I/O-facing building blocks such as shared
    HTTP transport creation, Redis access, circuit-breaker state storage, and
    secret retrieval backends. Business rules should stay in services, not here.

Step-by-step relationship flow:
    1. Startup wires infrastructure dependencies from settings.
    2. Provider construction asks ``HTTPClientFactory`` for transport clients.
    3. Authorization/config layers use ``RedisCache`` for cache/pub-sub paths.
    4. Provider execution uses circuit breakers backed by Redis where possible.
    5. Provider credentials are fetched via ``SecretStore`` implementations.

Subpackages:
    - ``provider_credentials``: secret store contracts plus concrete backends
      (environment, AES-GCM encrypted DB, Vault KV v2).

Author: Shubham Singh
"""

from app.infrastructure.http_client_factory import HTTPClientFactory
from app.infrastructure.provider_credentials import (
    AESGCMSecretStore,
    EnvironmentSecretStore,
    SecretStore,
    VaultSecretStore,
)
from app.infrastructure.redis_cache import RedisCache

__all__ = [
    "AESGCMSecretStore",
    "EnvironmentSecretStore",
    "HTTPClientFactory",
    "RedisCache",
    "SecretStore",
    "VaultSecretStore",
]
