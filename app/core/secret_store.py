"""
SecretStore — Secure retrieval and decryption of provider API keys.

Provider API keys are stored encrypted in PostgreSQL or in HashiCorp Vault.
This module:
    1. Defines SecretStore — abstract async interface for fetching a secret
    2. Implements EnvironmentSecretStore — reads from env vars (dev/test)
    3. Implements AESGCMSecretStore — decrypts AES-256-GCM ciphertext from DB
    4. Implements VaultSecretStore — reads from Vault KV v2 via userpass auth
    5. Provides derive_tenant_key() — HKDF derivation of per-tenant keys

Key security properties:
    - Master key lives in environment / Secrets Manager (never in code)
    - Per-tenant keys derived via HKDF(master_key + tenant_id) — compromise
      of one tenant's key does not expose others
    - Decrypted secrets exist only in-memory during provider build
    - AES-256-GCM provides authenticated encryption (prevents tampering)
    - VaultSecretStore tokens are cached in-memory and refreshed on expiry

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

    HashiCorp Vault (KV v2):
          VAULT_USERNAME / VAULT_PASSWORD
          │
          ▼
    VaultSecretStore._ensure_token()   ← userpass login, token cached
          │
          ▼
    VaultSecretStore.get_secret()      ← GET /v1/{mount}/data/{prefix}/{ref}
          │
          ▼
    plaintext api_key field value

Dependencies:
    - cryptography >= 41.0  — AES-GCM, HKDF
    - httpx >= 0.28         — async Vault API calls

Author: Engineering Team
Last Updated: 2026-05-17
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from abc import ABC, abstractmethod

import httpx
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
    """Abstract async interface for fetching decrypted provider API keys.

    Implementations differ in WHERE they fetch secrets from (env, DB, Vault),
    but callers depend only on this interface — the Dependency Inversion principle.

    All implementations are async so that network-backed stores (Vault) can
    perform I/O without blocking. Sync stores (EnvironmentSecretStore,
    AESGCMSecretStore) use `async def` without internal awaits — that is fine.

    Example:
        >>> store: SecretStore = EnvironmentSecretStore()
        >>> api_key = await store.get_secret("OPENAI_API_KEY", tenant_id="acme")
        >>> api_key.startswith("sk-")
        True
    """

    @abstractmethod
    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Retrieve a plaintext secret by its reference string.

        Args:
            secret_reference: Opaque reference as stored in DeploymentConfig
                              (e.g., 'OPENAI_API_KEY' or 'providers/openai/default').
            tenant_id: UUID string of the requesting tenant. Used for key
                       derivation in encrypted stores.

        Returns:
            Plaintext secret value. Never log or persist this value.

        Raises:
            KeyError: If the secret_reference is not found.
            ValueError: If decryption fails (tampered ciphertext).
        """

    async def aclose(self) -> None:
        """Release any resources held by this store (e.g. HTTP client).

        Default implementation is a no-op. Override in network-backed stores.
        Called once during application shutdown.
        """
        return


# ── Environment Implementation ────────────────────────────────────────────────


class EnvironmentSecretStore(SecretStore):
    """Fetches secrets from environment variables.

    For use in development and CI only. The secret_reference is treated as
    an environment variable name. No encryption/decryption performed.

    Example:
        >>> import os
        >>> os.environ["OPENAI_API_KEY"] = "sk-test"
        >>> store = EnvironmentSecretStore()
        >>> await store.get_secret("OPENAI_API_KEY", tenant_id="any")
        'sk-test'
    """

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
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
        >>> api_key = await store.get_secret("secret/acme/openai", tenant_id="acme-uuid")
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

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
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


# ── HashiCorp Vault KV v2 Implementation ─────────────────────────────────────

# Fraction of the token's lease_duration to use as a refresh buffer.
# Refreshing at 90% of the TTL prevents clock-skew edge cases.
_VAULT_TOKEN_REFRESH_FRACTION: float = 0.9


class VaultSecretStore(SecretStore):
    """Fetches secrets from HashiCorp Vault KV v2 using userpass auth.

    Authenticates once via the userpass auth method, caches the returned
    Vault token in memory, and re-authenticates automatically when the token
    is within 10% of its lease expiry. All network I/O is async (httpx).

    Path convention (KV v2):
        {mount_path}/data/{kv_prefix}/{secret_reference}
        e.g.  secret/data/llm-provider-service/providers/openai/default

    The secret stored at that path must have an ``api_key`` field:
        vault kv put secret/llm-provider-service/providers/openai/default \\
              api_key="sk-..."

    Example:
        >>> store = VaultSecretStore(
        ...     vault_addr="http://localhost:8200",
        ...     username="llm-service",
        ...     password="s3cr3t",
        ... )
        >>> api_key = await store.get_secret(
        ...     "providers/openai/default", tenant_id="acme-uuid"
        ... )
    """

    def __init__(
        self,
        vault_addr: str,
        username: str,
        password: str,
        mount_path: str = "secret",
        kv_prefix: str = "llm-provider-service",
    ) -> None:
        """Initialise the Vault client. No network calls are made here.

        Args:
            vault_addr: Vault server address, e.g. "http://vault:8200".
            username: Userpass auth username for the service account.
            password: Plaintext password (extracted from SecretStr by caller).
            mount_path: KV v2 mount path (default "secret").
            kv_prefix: Path prefix within the mount (default "llm-provider-service").
        """
        self._vault_addr = vault_addr.rstrip("/")
        self._username = username
        self._password = password
        self._mount_path = mount_path.strip("/")
        self._kv_prefix = kv_prefix.strip("/")

        self._client = httpx.AsyncClient(
            base_url=self._vault_addr,
            timeout=httpx.Timeout(10.0),
            headers={"Content-Type": "application/json"},
        )
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _ensure_token(self) -> str:
        """Return a valid Vault token, refreshing via userpass login if expired.

        Uses double-checked locking so concurrent requests don't all trigger
        a re-authentication simultaneously.
        """
        # Fast path — token still within its valid window
        if self._token is not None and time.monotonic() < self._token_expiry:
            return self._token

        async with self._token_lock:
            # Double-check after acquiring the lock
            if self._token is not None and time.monotonic() < self._token_expiry:
                return self._token

            response = await self._client.post(
                f"/v1/auth/userpass/login/{self._username}",
                json={"password": self._password},
            )
            if response.status_code == 400:
                raise PermissionError(
                    f"Vault userpass login failed for '{self._username}': bad credentials."
                )
            response.raise_for_status()

            auth_payload: dict[str, object] = response.json().get("auth", {})  # type: ignore[assignment]
            token = auth_payload.get("client_token")
            if not isinstance(token, str):
                raise RuntimeError(
                    "Vault login response did not contain 'auth.client_token'."
                )
            raw_lease = auth_payload.get("lease_duration", 3600)
            lease_duration = raw_lease if isinstance(raw_lease, int) else 3600

            self._token = token
            # Cache for _VAULT_TOKEN_REFRESH_FRACTION of the lease to avoid expiry races.
            self._token_expiry = time.monotonic() + (
                lease_duration * _VAULT_TOKEN_REFRESH_FRACTION
            )

            logger.debug(
                "Vault token acquired",
                extra={"username": self._username, "lease_duration": lease_duration},
            )
            return self._token

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Read a secret from Vault KV v2 and return the value of its ``api_key`` field.

        Args:
            secret_reference: Path within kv_prefix, e.g. 'providers/openai/default'.
                              This is the value stored in DeploymentConfig.secret_reference.
            tenant_id: Not used for path-based Vault access control, but retained
                       for interface compatibility and audit logging.

        Returns:
            Plaintext value of the ``api_key`` field at the resolved Vault path.

        Raises:
            KeyError: If the secret path does not exist in Vault or has no ``api_key`` field.
            PermissionError: If Vault auth fails.
            httpx.HTTPStatusError: On unexpected Vault HTTP errors.
        """
        token = await self._ensure_token()

        # KV v2 read path: /v1/{mount}/data/{prefix}/{reference}
        path = f"/v1/{self._mount_path}/data/{self._kv_prefix}/{secret_reference.strip('/')}"

        response = await self._client.get(
            path,
            headers={"X-Vault-Token": token},
        )

        if response.status_code == 404:
            raise KeyError(
                f"Vault secret not found at '{self._mount_path}/{self._kv_prefix}/"
                f"{secret_reference}'. Ensure the secret was written to Vault."
            )
        if response.status_code == 403:
            raise PermissionError(
                f"Vault permission denied for path '{self._mount_path}/{self._kv_prefix}/"
                f"{secret_reference}'. Check the service account policy."
            )
        response.raise_for_status()

        # KV v2 response envelope: {"data": {"data": {"api_key": "..."}, "metadata": {...}}}
        secret_data: dict[str, str] = response.json().get("data", {}).get("data", {})
        api_key: str | None = secret_data.get("api_key")

        if api_key is None:
            raise KeyError(
                f"Vault secret at '{secret_reference}' has no 'api_key' field. "
                f"Available fields: {sorted(secret_data.keys())}"
            )

        logger.debug(
            "Secret retrieved from Vault",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return api_key

    async def aclose(self) -> None:
        """Close the underlying httpx client. Called during application shutdown."""
        await self._client.aclose()
        logger.debug("VaultSecretStore HTTP client closed")
