"""Behavioral tests for the write-only Vault adapter."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.adapters.secret_management import VaultClientOptions, VaultSecretWriter
from app.core.exceptions import SecretBackendUnavailableError


def _response(payload: dict[str, object] | None = None, *, status: int = 200) -> MagicMock:
    response = MagicMock(status_code=status)
    response.json.return_value = payload or {}
    return response


def _login_response() -> MagicMock:
    return _response({"auth": {"client_token": "token", "lease_duration": 60}})


@pytest.mark.asyncio
async def test_writer_retries_transient_write_without_logging_secret() -> None:
    """A bounded retry can safely repeat the same KV-v2 upsert payload."""
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[_login_response(), httpx.ConnectError("offline"), _response()]
    )
    writer = VaultSecretWriter(
        "http://vault.test",
        "writer",
        "password",
        0.9,
        client_options=VaultClientOptions(
            retry_base_delay_seconds=0.001,
            retry_max_delay_seconds=0.001,
        ),
        http_client=cast("httpx.AsyncClient", client),
    )

    reference = await writer.write_secret(
        "tenant-a/openai",
        tenant_id="tenant-a",
        fields={"api_key": "never-log-this"},
    )

    assert reference == "tenant-a/openai"
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_writer_translates_exhausted_transport_failure() -> None:
    """Write-side dependency outages use the same stable 503-domain error."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=[_login_response(), httpx.ReadTimeout("slow")])
    writer = VaultSecretWriter(
        "http://vault.test",
        "writer",
        "password",
        0.9,
        client_options=VaultClientOptions(request_max_attempts=1),
        http_client=cast("httpx.AsyncClient", client),
    )

    with pytest.raises(SecretBackendUnavailableError):
        await writer.write_secret(
            "tenant-a/openai",
            tenant_id="tenant-a",
            fields={"api_key": "secret"},
        )


@pytest.mark.asyncio
async def test_writer_rejects_empty_payload_before_network_write() -> None:
    """Dropping null fields cannot silently create an empty credential record."""
    client = MagicMock()
    client.post = AsyncMock()
    writer = VaultSecretWriter(
        "http://vault.test",
        "writer",
        "password",
        0.9,
        http_client=cast("httpx.AsyncClient", client),
    )

    with pytest.raises(ValueError, match="non-null"):
        await writer.write_secret(
            "tenant-a/openai",
            tenant_id="tenant-a",
            fields={"api_key": None},
        )

    client.post.assert_not_awaited()
