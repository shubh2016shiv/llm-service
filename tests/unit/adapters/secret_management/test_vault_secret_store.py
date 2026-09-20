"""Behavioral tests for Vault token refresh policy."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.secret_management import VaultClientOptions, VaultSecretStore
from app.api.exception_handlers import _INFERENCE_EXCEPTION_STATUS, _resolve_status
from app.core.exceptions import SecretBackendUnavailableError


@pytest.mark.asyncio
async def test_get_secret_refreshes_token_at_configured_lease_fraction() -> None:
    """REQ: cached Vault authentication expires at the configured lease fraction."""
    login_response = build_response(
        {"auth": {"client_token": "vault-token", "lease_duration": 100}}
    )
    secret_response = build_response({"data": {"data": {"api_key": "provider-key"}}})
    vault_client = MagicMock()
    vault_client.post = AsyncMock(return_value=login_response)
    vault_client.get = AsyncMock(return_value=secret_response)
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    with patch(
        "app.adapters.secret_management.vault_client.time.monotonic",
        return_value=1_000.0,
    ):
        first_secret = await store.get_secret("openai/default", tenant_id="tenant-a")
    with patch(
        "app.adapters.secret_management.vault_client.time.monotonic",
        return_value=1_049.0,
    ):
        second_secret = await store.get_secret("openai/default", tenant_id="tenant-a")
    with patch(
        "app.adapters.secret_management.vault_client.time.monotonic",
        return_value=1_050.0,
    ):
        third_secret = await store.get_secret("openai/default", tenant_id="tenant-a")

    assert first_secret == second_secret == third_secret == "provider-key"
    assert vault_client.post.await_count == 2


@pytest.mark.parametrize("invalid_fraction", [0.0, 1.0])
def test_vault_secret_store_with_invalid_refresh_fraction_rejects_value(
    invalid_fraction: float,
) -> None:
    """REQ: direct adapter construction cannot bypass lease-policy validation."""
    with pytest.raises(ValueError, match="greater than 0 and less than 1"):
        VaultSecretStore(
            vault_addr="http://vault.test",
            username="service-user",
            password="service-password",
            token_refresh_after_lease_fraction=invalid_fraction,
        )


def build_response(payload: dict[str, object]) -> MagicMock:
    """Return a successful HTTP response double with controlled JSON data."""
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    return response


@pytest.mark.asyncio
async def test_login_retries_transient_failures_then_succeeds() -> None:
    """A network blip during login is retried instead of failing the request."""
    login_response = build_response(
        {"auth": {"client_token": "vault-token", "lease_duration": 100}}
    )
    secret_response = build_response({"data": {"data": {"api_key": "provider-key"}}})
    vault_client = MagicMock()
    vault_client.post = AsyncMock(
        side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.ConnectError("connection refused"),
            login_response,
        ]
    )
    vault_client.get = AsyncMock(return_value=secret_response)
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        client_options=VaultClientOptions(
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.01,
        ),
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    secret = await store.get_secret("openai/default", tenant_id="tenant-a")

    assert secret == "provider-key"
    assert vault_client.post.await_count == 3


@pytest.mark.asyncio
async def test_login_does_not_retry_bad_credentials() -> None:
    """A wrong password fails immediately — retrying cannot fix it."""
    vault_client = MagicMock()
    vault_client.post = AsyncMock(return_value=MagicMock(status_code=400))
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        client_options=VaultClientOptions(
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.01,
        ),
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    with pytest.raises(PermissionError, match="bad credentials"):
        await store.get_secret("openai/default", tenant_id="tenant-a")

    assert vault_client.post.await_count == 1


@pytest.mark.asyncio
async def test_login_gives_up_after_max_attempts() -> None:
    """Persistent failures fail the request after the configured attempts."""
    vault_client = MagicMock()
    vault_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        client_options=VaultClientOptions(
            request_max_attempts=3,
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.01,
        ),
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    # Surfaces as a domain error, not the raw httpx failure: an unreachable
    # Vault is a dependency outage the API must report as 503, and an
    # httpx error is not an LLMServiceError so it would bypass domain
    # translation and surface as an untyped 500 instead.
    with pytest.raises(SecretBackendUnavailableError) as exc_info:
        await store.get_secret("openai/default", tenant_id="tenant-a")

    assert vault_client.post.await_count == 3
    # The original transport failure is preserved for debugging.
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
    assert exc_info.value.backend_name == "vault"


@pytest.mark.asyncio
async def test_unreachable_vault_during_read_raises_domain_error() -> None:
    """REQ: a transport failure on the secret GET (not just login) is also
    translated, so a mid-read outage cannot escape as an untyped 500.
    """
    login_response = build_response(
        {"auth": {"client_token": "vault-token", "lease_duration": 100}}
    )
    vault_client = MagicMock()
    vault_client.post = AsyncMock(return_value=login_response)
    vault_client.get = AsyncMock(side_effect=httpx.ReadTimeout("read timed out"))
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    with pytest.raises(SecretBackendUnavailableError) as exc_info:
        await store.get_secret("openai/default", tenant_id="tenant-a")

    assert exc_info.value.secret_reference == "openai/default"
    assert vault_client.get.await_count == 3


@pytest.mark.asyncio
async def test_vault_server_error_raises_domain_error() -> None:
    """REQ: a Vault 5xx is an availability problem, not a caller problem."""
    login_response = build_response(
        {"auth": {"client_token": "vault-token", "lease_duration": 100}}
    )
    server_error = MagicMock(status_code=503)
    server_error.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=MagicMock(status_code=503)
    )
    vault_client = MagicMock()
    vault_client.post = AsyncMock(return_value=login_response)
    vault_client.get = AsyncMock(return_value=server_error)
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    with pytest.raises(SecretBackendUnavailableError):
        await store.get_secret("openai/default", tenant_id="tenant-a")


@pytest.mark.asyncio
async def test_missing_secret_still_raises_keyerror() -> None:
    """REQ: the documented contract is preserved.

    The outage wrap catches httpx.HTTPError only. KeyError is not a subclass,
    so a genuinely missing secret must still surface as KeyError rather than
    being mislabelled an outage — the two need different remediation, and a
    retry will never fix a secret that was never written.
    """
    login_response = build_response(
        {"auth": {"client_token": "vault-token", "lease_duration": 100}}
    )
    vault_client = MagicMock()
    vault_client.post = AsyncMock(return_value=login_response)
    vault_client.get = AsyncMock(return_value=MagicMock(status_code=404))
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    with pytest.raises(KeyError):
        await store.get_secret("openai/default", tenant_id="tenant-a")


@pytest.mark.asyncio
async def test_denied_secret_still_raises_permissionerror() -> None:
    """REQ: a 403 remains PermissionError, for the same contract reason."""
    login_response = build_response(
        {"auth": {"client_token": "vault-token", "lease_duration": 100}}
    )
    vault_client = MagicMock()
    vault_client.post = AsyncMock(return_value=login_response)
    vault_client.get = AsyncMock(return_value=MagicMock(status_code=403))
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    with pytest.raises(PermissionError):
        await store.get_secret("openai/default", tenant_id="tenant-a")


def test_secret_backend_unavailable_maps_to_503() -> None:
    """REQ: the new error resolves to 503 on the inference path, replacing the
    untyped 500 a raw httpx error produced.
    """
    exc = SecretBackendUnavailableError(
        backend_name="vault", secret_reference="openai/default", reason="ConnectError"
    )

    assert _resolve_status(exc, _INFERENCE_EXCEPTION_STATUS) == 503


@pytest.mark.asyncio
async def test_close_does_not_close_borrowed_client_and_rejects_reuse() -> None:
    """Dependency injection transfers use, not ownership, of the HTTP client."""
    vault_client = MagicMock()
    vault_client.aclose = AsyncMock()
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", vault_client),
    )

    await store.aclose()
    await store.aclose()

    vault_client.aclose.assert_not_awaited()
    with pytest.raises(RuntimeError, match="is closed"):
        await store.get_secret("openai/default", tenant_id="tenant-a")


def test_vault_path_rejects_parent_segments() -> None:
    """References cannot escape their configured KV prefix."""
    store = VaultSecretStore(
        vault_addr="http://vault.test",
        username="service-user",
        password="service-password",
        token_refresh_after_lease_fraction=0.5,
        http_client=cast("httpx.AsyncClient", MagicMock()),
    )

    with pytest.raises(ValueError, match="path segments"):
        store._vault.kv_path("tenant-a/../tenant-b")
