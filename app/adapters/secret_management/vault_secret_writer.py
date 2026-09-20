"""Write-only HashiCorp Vault adapter used by credential management flows."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.adapters.secret_management.vault_client import (
    VaultClient,
    VaultClientOptions,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

logger = logging.getLogger(__name__)


class VaultSecretWriter:
    """Store credentials with a write-only Vault identity.

    This class deliberately has no read method. Even if application code is
    compromised through a management endpoint, this object cannot be used to
    retrieve existing provider credentials.
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
        """Create the authenticated write gateway from validated settings."""
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

    async def write_secret(
        self,
        path: str,
        *,
        tenant_id: str,
        fields: Mapping[str, str | None],
    ) -> str:
        """Write one non-empty KV-v2 payload and return its canonical path."""
        write_path = self._vault.kv_path(path)
        payload = {key: value for key, value in fields.items() if value is not None}
        if not payload:
            raise ValueError("Vault secret fields must contain at least one non-null value.")
        if any(not key for key in payload):
            raise ValueError("Vault secret field names must not be empty.")

        response = await self._vault.request(
            "POST",
            write_path,
            secret_reference=path,
            json={"data": payload},
        )
        if response.status_code in {401, 403}:
            raise PermissionError(
                f"Vault permission denied writing secret reference {path!r}. "
                "Check the write service-account policy."
            )
        if response.status_code >= 400:
            raise ValueError(
                f"Vault rejected secret reference {path!r} with HTTP {response.status_code}."
            )
        response.raise_for_status()
        logger.debug(
            "Secret written to Vault",
            extra={"secret_reference": path, "tenant_id": tenant_id},
        )
        return path.strip("/")

    async def aclose(self) -> None:
        """Close owned networking resources; safe to call more than once."""
        await self._vault.aclose()
