"""
Vault Secret Store
==================

HashiCorp Vault KV v2 secret backend using userpass authentication.

Jargon explained:
    - KV v2: Vault key/value secrets engine with versioned secret data.
    - Lease duration: token validity window returned by Vault auth response.
    - Refresh buffer: proactive refresh before actual expiry to avoid race
      conditions near token expiration.

Step-by-step flow:
    1. ``get_secret`` asks ``_ensure_token`` for a valid Vault token.
    2. ``_ensure_token`` reuses cached token or performs userpass login.
    3. Store reads KV v2 path for requested reference.
    4. ``api_key`` field is extracted and returned as plaintext.

Best-practice notes:
    - Scope Vault policies to minimum required prefixes.
    - Rotate Vault credentials and audit access logs.
    - Avoid logging secret values; log only references and tenant context.

Author: Shubham Singh
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.infrastructure.provider_credentials.contracts import SecretStore

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_FRACTION: float = 0.9


class VaultSecretStore(SecretStore):
    """Read provider credentials from Vault KV v2 with token caching.

    What this class does:
        It handles Vault login, token reuse, token refresh, and secret fetches
        so callers do not need to implement Vault protocol logic themselves.

    Why token caching exists:
        Logging in on every secret request is slow and increases load on Vault.
        Cached tokens reduce overhead while lock-guarded refresh avoids race
        conditions under concurrent request load.

    Example:
        >>> # store = VaultSecretStore(
        >>> #     vault_addr="http://localhost:8200",
        >>> #     username="llm-service",
        >>> #     password="***",
        >>> #     mount_path="secret",
        >>> #     kv_prefix="llm-provider-service",
        >>> # )
        >>> # key = await store.get_secret("providers/openai/default", tenant_id="tenant-a")
    """

    def __init__(
        self,
        vault_addr: str,
        username: str,
        password: str,
        mount_path: str = "secret",
        kv_prefix: str = "llm-provider-service",
    ) -> None:
        """Create HTTP client and initialize token cache state.

        Args:
            vault_addr: Vault base URL.
            username: Userpass username for Vault login.
            password: Userpass password for Vault login.
            mount_path: KV mount path (for example ``secret``).
            kv_prefix: Prefix under mount where service secrets are stored.
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
        """Return valid token, refreshing via userpass login when needed.

        Double-checked locking avoids simultaneous token refresh attempts from
        concurrent requests.

        Rationale:
            Token refresh is relatively expensive and must be serialized to
            prevent many workers from flooding Vault during expiry windows.
        """
        if self._token is not None and time.monotonic() < self._token_expiry:
            return self._token

        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_expiry:
                return self._token

            response = await self._client.post(
                f"/v1/auth/userpass/login/{self._username}",
                json={"password": self._password},
            )
            if response.status_code == 400:
                raise PermissionError(
                    f"Vault userpass login failed for {self._username!r}: bad credentials."
                )
            response.raise_for_status()

            auth_payload: dict[str, object] = response.json().get("auth", {})  # type: ignore[assignment]
            token = auth_payload.get("client_token")
            if not isinstance(token, str):
                raise RuntimeError("Vault login response missing auth.client_token.")
            raw_lease = auth_payload.get("lease_duration", 3600)
            lease_duration = raw_lease if isinstance(raw_lease, int) else 3600

            self._token = token
            self._token_expiry = time.monotonic() + (lease_duration * _TOKEN_REFRESH_FRACTION)

            logger.debug(
                "Vault token acquired",
                extra={"username": self._username, "lease_duration": lease_duration},
            )
            return self._token

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Read KV v2 secret and return ``api_key`` field value.

        Args:
            secret_reference: Relative secret path under configured prefix.
            tenant_id: Tenant context used for logging/audit metadata.

        Returns:
            Plaintext API key string.

        Step-by-step:
            1. Ensure a valid token exists.
            2. Build KV v2 read path.
            3. Handle 404/403 explicitly with actionable errors.
            4. Extract ``api_key`` field and return it.
        """
        token = await self._ensure_token()
        path = f"/v1/{self._mount_path}/data/{self._kv_prefix}/{secret_reference.strip('/')}"
        response = await self._client.get(path, headers={"X-Vault-Token": token})

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
        """Close underlying HTTP client during application shutdown.

        Always call from application lifespan shutdown hooks.
        """
        await self._client.aclose()
        logger.debug("VaultSecretStore HTTP client closed")
