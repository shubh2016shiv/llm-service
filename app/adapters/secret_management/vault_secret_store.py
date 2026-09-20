"""Read-only HashiCorp Vault KV-v2 secret adapter.

Reading this package for the first time?
    ``vault_client.py`` explains authentication, retries, and HTTP ownership.
    This file intentionally contains only the read-side domain translation:
    Vault response -> plaintext ``api_key`` or a precise application error.

The read and write adapters use different Vault identities. Keeping them as
different classes makes least privilege visible in code: inference can read but
cannot overwrite credentials; management can write but cannot read them back.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.adapters.secret_management.secret_store import SecretStore
from app.adapters.secret_management.vault_client import (
    VaultClient,
    VaultClientOptions,
)
from app.core.exceptions import SecretBackendUnavailableError

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


class VaultSecretStore(SecretStore):
    """Resolve provider API keys with a read-only Vault identity.

    Request flow:
        1. Validate and build the KV-v2 path.
        2. Let ``VaultClient`` authenticate and retry transient failures.
        3. Translate 403/404 into stable domain errors.
        4. Validate the nested KV-v2 payload and return only ``api_key``.
    """

    def __init__(
        self,
        vault_addr: str,
        username: str,
        password: str,
        token_refresh_after_lease_fraction: float,
        mount_path: str = "secret",
        kv_prefix: str = "llm-provider-service",
        client_options: VaultClientOptions | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the authenticated read gateway from validated settings."""
        self._vault = VaultClient(
            vault_addr=vault_addr,
            username=username,
            password=password,
            token_refresh_after_lease_fraction=token_refresh_after_lease_fraction,
            mount_path=mount_path,
            kv_prefix=kv_prefix,
            options=client_options,
            http_client=http_client,
        )

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Return a validated ``api_key`` from one Vault KV-v2 record.

        Secret values and response bodies are never included in logs or error
        messages. A malformed successful response is classified as backend
        unavailability because Vault did not satisfy its response contract.
        """
        path = self._vault.kv_path(secret_reference)
        response = await self._vault.request(
            "GET",
            path,
            secret_reference=secret_reference,
        )
        if response.status_code == 404:
            raise KeyError(
                f"Vault secret reference {secret_reference!r} was not found. "
                "Ensure it was written to the configured mount and prefix."
            )
        if response.status_code in {401, 403}:
            raise PermissionError(
                f"Vault permission denied for secret reference {secret_reference!r}. "
                "Check the read service-account policy."
            )
        if response.status_code >= 400:
            raise ValueError(
                f"Vault rejected secret reference {secret_reference!r} "
                f"with HTTP {response.status_code}."
            )
        response.raise_for_status()

        try:
            payload = response.json()
            secret_data = payload["data"]["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SecretBackendUnavailableError(
                backend_name="vault",
                secret_reference=secret_reference,
                reason="invalid KV-v2 response payload",
            ) from exc
        if not isinstance(secret_data, dict):
            raise SecretBackendUnavailableError(
                backend_name="vault",
                secret_reference=secret_reference,
                reason="invalid KV-v2 secret data",
            )

        api_key = secret_data.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            available_fields = sorted(str(field) for field in secret_data)
            raise KeyError(
                f"Vault secret at {secret_reference!r} has no non-empty 'api_key' field. "
                f"Available fields: {available_fields}"
            )

        logger.debug(
            "Secret retrieved from Vault",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return api_key

    async def aclose(self) -> None:
        """Close owned networking resources; safe to call more than once."""
        await self._vault.aclose()
