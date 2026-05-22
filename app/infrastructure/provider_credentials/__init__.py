"""
Provider Credentials Package
============================

Secret retrieval abstractions and implementations used to obtain provider API
credentials safely at runtime.

Backends:
    - ``EnvironmentSecretStore``: reads secret values from environment vars.
    - ``AESGCMSecretStore``: decrypts AES-GCM ciphertext stored in persistence.
    - ``VaultSecretStore``: reads secret fields from HashiCorp Vault KV v2.

Step-by-step relationship flow:
    1. Deployment/entitlement config provides ``secret_reference``.
    2. Provider builder calls ``SecretStore.get_secret(secret_reference, tenant_id=...)``.
    3. Chosen backend resolves and returns plaintext in-memory.
    4. Provider request uses plaintext key, which is never persisted back.

Best-practice reminder:
    Keep plaintext secrets in memory only for the minimal lifetime needed to
    build request headers or auth clients.

Author: Shubham Singh
"""

from app.infrastructure.provider_credentials.aesgcm import AESGCMSecretStore
from app.infrastructure.provider_credentials.contracts import SecretStore
from app.infrastructure.provider_credentials.crypto import (
    decrypt_api_key,
    derive_tenant_key,
    encrypt_api_key,
)
from app.infrastructure.provider_credentials.environment import EnvironmentSecretStore
from app.infrastructure.provider_credentials.vault import VaultSecretStore

__all__ = [
    "AESGCMSecretStore",
    "EnvironmentSecretStore",
    "SecretStore",
    "VaultSecretStore",
    "decrypt_api_key",
    "derive_tenant_key",
    "encrypt_api_key",
]
