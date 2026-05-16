"""
SecretStore — Secure retrieval and decryption of provider API keys.

Provider API keys are stored encrypted in PostgreSQL. This module:
    1. Defines SecretStore — abstract interface for fetching a secret
    2. Implements EnvironmentSecretStore — reads from env vars (dev/test)
    3. Implements AESGCMSecretStore — decrypts AES-256-GCM ciphertext from DB
    4. Provides derive_tenant_key() — HKDF derivation of per-tenant keys

Key security properties:
    - Master key lives in environment / Secrets Manager (never in code)
    - Per-tenant keys derived via HKDF(master_key + tenant_id) — compromise
      of one tenant's key does not expose others
    - Decrypted secrets exist only in-memory during provider build
    - AES-256-GCM provides authenticated encryption (prevents tampering)

Architecture:
-------------
    ApplicationSettings.encryption_master_key (env var)
          │
          ▼
    AESGCMSecretStore.derive_tenant_key(tenant_id)   ← HKDF
          │
          ▼
    AESGCMSecretStore.decrypt(ciphertext, tenant_id)  ← AES-256-GCM
          │
          ▼
    plaintext API key (in-memory only, injected into provider headers)

Dependencies:
    - cryptography >= 41.0  — AES-GCM, HKDF

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

import base64
import logging
import os
from abc import ABC, abstractmethod

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

# AES-GCM nonce length in bytes (96-bit nonce is NIST recommended for GCM).
_NONCE_BYTES: int = 12
# AES-256 key length in bytes.
_KEY_BYTES: int = 32
# HKDF info string differentiates derived keys from the same master.
_HKDF_INFO: bytes = b"llm-provider-service:tenant-api-key"


# ── Key Derivation ────────────────────────────────────────────────────────────


def derive_tenant_key(master_key_bytes: bytes, tenant_id: str) -> bytes:
    """Derive a 256-bit AES key for a specific tenant via HKDF-SHA256.

    WHY: Using HKDF over direct key reuse means each tenant has a unique
    derived key. Compromising one tenant's storage key does not enable
    decryption of any other tenant's ciphertexts.

    Args:
        master_key_bytes: Raw 32-byte master key from ApplicationSettings.
        tenant_id: UUID string of the tenant (used as the HKDF salt).

    Returns:
        32-byte derived key specific to this tenant.

    Example:
        >>> key = derive_tenant_key(master_key_bytes, "acme-tenant-uuid")
        >>> len(key)
        32
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=tenant_id.encode("utf-8"),
        info=_HKDF_INFO,
    )
    return hkdf.derive(master_key_bytes)


def encrypt_api_key(plaintext: str, master_key_bytes: bytes, tenant_id: str) -> str:
    """Encrypt a plaintext API key for storage in PostgreSQL.

    For use in admin tooling when storing a new tenant API key.
    Produces base64url-encoded ciphertext: nonce || ciphertext.

    Args:
        plaintext: The raw API key to encrypt.
        master_key_bytes: Raw 32-byte master key.
        tenant_id: UUID string of the owning tenant.

    Returns:
        Base64url-encoded string: nonce (12 bytes) + AES-GCM ciphertext.

    Example:
        >>> ciphertext = encrypt_api_key("sk-abc123", master_key_bytes, tenant_id)
        >>> isinstance(ciphertext, str)
        True
    """
    derived_key: bytes = derive_tenant_key(master_key_bytes, tenant_id)
    aesgcm = AESGCM(derived_key)
    nonce: bytes = os.urandom(_NONCE_BYTES)
    encrypted: bytes = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_api_key(ciphertext_b64: str, master_key_bytes: bytes, tenant_id: str) -> str:
    """Decrypt an AES-256-GCM encrypted API key from PostgreSQL.

    Args:
        ciphertext_b64: Base64url-encoded nonce + ciphertext from the DB.
        master_key_bytes: Raw 32-byte master key from ApplicationSettings.
        tenant_id: UUID string of the owning tenant (used for key derivation).

    Returns:
        Plaintext API key string.

    Raises:
        ValueError: If the ciphertext is too short to contain a valid nonce.
        cryptography.exceptions.InvalidTag: If authentication fails (tampered data).

    Example:
        >>> key = decrypt_api_key(stored_ciphertext, master_key_bytes, tenant_id)
        >>> key.startswith("sk-")
        True
    """
    raw: bytes = base64.urlsafe_b64decode(ciphertext_b64.encode("ascii"))
    if len(raw) <= _NONCE_BYTES:
        raise ValueError(
            f"Ciphertext too short: expected >{_NONCE_BYTES} bytes, got {len(raw)}."
        )
    nonce: bytes = raw[:_NONCE_BYTES]
    ciphertext: bytes = raw[_NONCE_BYTES:]
    derived_key: bytes = derive_tenant_key(master_key_bytes, tenant_id)
    aesgcm = AESGCM(derived_key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


# ── Abstract Interface ────────────────────────────────────────────────────────


class SecretStore(ABC):
    """Abstract interface for fetching decrypted provider API keys.

    Implementations differ in WHERE they fetch secrets from (env, DB, Vault),
    but callers depend only on this interface — the Dependency Inversion principle.

    Example:
        >>> store: SecretStore = EnvironmentSecretStore()
        >>> api_key = store.get_secret("OPENAI_API_KEY", tenant_id="acme")
        >>> api_key.startswith("sk-")
        True
    """

    @abstractmethod
    def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Retrieve a plaintext secret by its reference string.

        Args:
            secret_reference: Opaque reference as stored in DeploymentConfig
                              (e.g., 'OPENAI_API_KEY' or 'secret/acme/openai').
            tenant_id: UUID string of the requesting tenant. Used for key
                       derivation in encrypted stores.

        Returns:
            Plaintext secret value. Never log or persist this value.

        Raises:
            KeyError: If the secret_reference is not found.
            ValueError: If decryption fails (tampered ciphertext).
        """


# ── Environment Implementation ────────────────────────────────────────────────


class EnvironmentSecretStore(SecretStore):
    """Fetches secrets from environment variables.

    For use in development and CI only. The secret_reference is treated as
    an environment variable name. No encryption/decryption performed.

    Example:
        >>> import os
        >>> os.environ["OPENAI_API_KEY"] = "sk-test"
        >>> store = EnvironmentSecretStore()
        >>> store.get_secret("OPENAI_API_KEY", tenant_id="any")
        'sk-test'
    """

    def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Look up the secret_reference as an environment variable name.

        Args:
            secret_reference: Environment variable name (e.g., 'OPENAI_API_KEY').
            tenant_id: Ignored in this implementation (no per-tenant derivation).

        Returns:
            Value of the environment variable.

        Raises:
            KeyError: If the environment variable is not set.
        """
        value: str | None = os.environ.get(secret_reference)
        if value is None:
            raise KeyError(
                f"Environment variable {secret_reference!r} is not set. "
                "Ensure it is defined in your .env file or shell environment."
            )
        logger.debug(
            "Secret retrieved from environment",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return value


# ── AES-GCM Database Implementation ──────────────────────────────────────────


class AESGCMSecretStore(SecretStore):
    """Decrypts AES-256-GCM ciphertexts fetched from PostgreSQL.

    The secret_reference is a key into a pre-loaded in-memory dict of
    {reference -> ciphertext_b64}. The in-memory dict is populated by the
    repository layer from the provider_credentials or tenant_deployments tables.

    WHY: The provider_credentials table stores only encrypted values. This
    store holds the master key in memory and decrypts on demand, so the
    plaintext never touches the DB.

    Example:
        >>> store = AESGCMSecretStore(
        ...     master_key_b64="<base64-encoded-32-bytes>",
        ...     encrypted_secrets={"secret/acme/openai": "<ciphertext>"},
        ... )
        >>> api_key = store.get_secret("secret/acme/openai", tenant_id="acme-uuid")
    """

    def __init__(
        self,
        master_key_b64: str,
        encrypted_secrets: dict[str, str] | None = None,
    ) -> None:
        """Initialise with the base64-encoded master key and optional pre-loaded secrets.

        Args:
            master_key_b64: Base64-encoded 32-byte master key
                            (from ApplicationSettings.encryption_master_key).
            encrypted_secrets: Pre-loaded {reference -> ciphertext_b64} mapping.
                               Can be empty; use register_secret() to add entries.
        """
        self._master_key_bytes: bytes = base64.b64decode(master_key_b64)
        if len(self._master_key_bytes) != _KEY_BYTES:
            raise ValueError(
                f"Master key must be exactly {_KEY_BYTES} bytes after base64 decoding, "
                f"got {len(self._master_key_bytes)} bytes."
            )
        # WHY: Mutable cache is safe here because secrets are registered at
        # startup (or lazily on first use) and never mutated during requests.
        self._encrypted_secrets: dict[str, str] = dict(encrypted_secrets or {})

    def register_secret(self, reference: str, ciphertext_b64: str) -> None:
        """Register an encrypted secret loaded from the database.

        Call this at startup or when a new deployment is created.

        Args:
            reference: Opaque reference string (e.g., 'secret/acme/openai').
            ciphertext_b64: AES-GCM ciphertext from the DB (base64url-encoded).
        """
        self._encrypted_secrets[reference] = ciphertext_b64

    def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Decrypt and return the plaintext secret for the given reference.

        Args:
            secret_reference: Opaque key into the in-memory ciphertext registry.
            tenant_id: UUID string of the owning tenant (used for key derivation).

        Returns:
            Plaintext API key string.

        Raises:
            KeyError: If secret_reference is not registered.
            ValueError: If decryption fails (wrong key or tampered data).
        """
        ciphertext_b64: str | None = self._encrypted_secrets.get(secret_reference)
        if ciphertext_b64 is None:
            raise KeyError(
                f"Secret reference {secret_reference!r} not found in store. "
                "Ensure it was registered at startup."
            )
        try:
            plaintext = decrypt_api_key(
                ciphertext_b64,
                master_key_bytes=self._master_key_bytes,
                tenant_id=tenant_id,
            )
        except (InvalidTag, ValueError) as exc:
            # WHY: Never include the ciphertext or any key material in the error.
            raise ValueError(
                f"Decryption failed for secret_reference={secret_reference!r}. "
                "Possible causes: wrong master key, wrong tenant_id, or tampered ciphertext."
            ) from exc

        logger.debug(
            "Secret decrypted successfully",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return plaintext
