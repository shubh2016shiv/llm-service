"""Authenticated, retrying HTTP client for HashiCorp Vault.

This module owns the network mechanics shared by the read-only and write-only
Vault adapters. Keeping them here prevents security-sensitive behavior—token
refresh, retry limits, path validation, and client ownership—from drifting
between the two identities.

The important ownership rule is simple: a client created here is closed here;
an injected client is borrowed and remains the caller's responsibility.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from app.core.exceptions import SecretBackendUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class VaultClientOptions:
    """Validated network and retry policy for one Vault identity.

    ``request_max_attempts`` counts the first request, so ``1`` disables
    retries. Full jitter chooses a random pause between zero and the current
    exponential cap; this prevents all service replicas retrying together.
    """

    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 10.0
    write_timeout_seconds: float = 10.0
    pool_timeout_seconds: float = 2.0
    request_max_attempts: int = 3
    retry_base_delay_seconds: float = 0.25
    retry_max_delay_seconds: float = 2.0

    def __post_init__(self) -> None:
        """Fail invalid operational settings during application startup."""
        timeout_values = (
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.write_timeout_seconds,
            self.pool_timeout_seconds,
        )
        if any(value <= 0 for value in timeout_values):
            raise ValueError("Vault HTTP timeouts must all be greater than zero.")
        if self.request_max_attempts < 1:
            raise ValueError("request_max_attempts must be at least 1.")
        if self.retry_base_delay_seconds <= 0 or self.retry_max_delay_seconds <= 0:
            raise ValueError("Vault retry delays must be greater than zero.")
        if self.retry_base_delay_seconds > self.retry_max_delay_seconds:
            raise ValueError("retry_base_delay_seconds cannot exceed retry_max_delay_seconds.")

    def retry_delay(self, failed_attempt: int) -> float:
        """Return a bounded full-jitter delay after one failed attempt."""
        exponential_cap = self.retry_base_delay_seconds * (2 ** (failed_attempt - 1))
        return random.uniform(0.0, min(exponential_cap, self.retry_max_delay_seconds))


class _VaultProtocolError(RuntimeError):
    """Vault answered, but its payload did not satisfy the expected protocol."""


class _VaultLoginUnavailable(RuntimeError):
    """The login retry budget was exhausted by a transient HTTP failure."""


class _VaultTokenSession:
    """Cache and refresh a userpass token for exactly one Vault identity."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        username: str,
        password: str,
        refresh_fraction: float,
        options: VaultClientOptions,
    ) -> None:
        if not username or not password:
            raise ValueError("Vault username and password must not be empty.")
        if not 0.0 < refresh_fraction < 1.0:
            raise ValueError("token refresh fraction must be greater than 0 and less than 1.")
        self._client = client
        self._username = username
        self._password = password
        self._refresh_fraction = refresh_fraction
        self._options = options
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = asyncio.Lock()

    async def ensure_token(self) -> str:
        """Return a cached token or serialize one bounded login sequence."""
        if self._token_is_fresh():
            return self._require_token()

        async with self._token_lock:
            if self._token_is_fresh():
                return self._require_token()

            for attempt in range(1, self._options.request_max_attempts + 1):
                try:
                    return await self._login_once()
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code < 500
                        and exc.response.status_code != 429
                    ):
                        raise
                    if attempt >= self._options.request_max_attempts:
                        raise _VaultLoginUnavailable from exc
                    delay = self._options.retry_delay(attempt)
                    logger.warning(
                        "Vault login failed; retrying",
                        extra={
                            "attempt": attempt,
                            "max_attempts": self._options.request_max_attempts,
                            "error_type": type(exc).__name__,
                            "retry_delay_seconds": round(delay, 3),
                        },
                    )
                    await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    def invalidate(self) -> None:
        """Forget a rejected or revoked token without retaining a stale copy."""
        self._token = None
        self._token_expiry = 0.0

    def _token_is_fresh(self) -> bool:
        return self._token is not None and time.monotonic() < self._token_expiry

    def _require_token(self) -> str:
        if self._token is None:
            raise AssertionError("fresh-token check succeeded without a token")
        return self._token

    async def _login_once(self) -> str:
        """Perform one login attempt and validate the complete token response."""
        safe_username = quote(self._username, safe="")
        response = await self._client.post(
            f"/v1/auth/userpass/login/{safe_username}",
            json={"password": self._password},
        )
        if response.status_code in {400, 401, 403}:
            raise PermissionError(
                f"Vault userpass login failed for {self._username!r}: bad credentials "
                "or authentication policy denied the request."
            )
        response.raise_for_status()

        try:
            payload = response.json()
            auth_payload = payload["auth"]
            token = auth_payload["client_token"]
            raw_lease = auth_payload.get("lease_duration", 3600)
        except (KeyError, TypeError, ValueError) as exc:
            raise _VaultProtocolError("Vault login response has an invalid auth payload.") from exc
        if not isinstance(token, str) or not token:
            raise _VaultProtocolError("Vault login response has no usable client token.")
        lease_duration = raw_lease if isinstance(raw_lease, int) and raw_lease > 0 else 3600

        self._token = token
        self._token_expiry = time.monotonic() + lease_duration * self._refresh_fraction
        logger.debug(
            "Vault token acquired",
            extra={"username": self._username, "lease_duration": lease_duration},
        )
        return token


class VaultClient:
    """Low-level authenticated Vault gateway shared by reader and writer.

    The gateway retries only failures that may recover: transport errors,
    timeouts, and HTTP 5xx responses. HTTP 4xx responses are returned to the
    higher-level adapter because 403 and 404 have domain-specific meanings.
    """

    def __init__(
        self,
        *,
        vault_addr: str,
        username: str,
        password: str,
        token_refresh_after_lease_fraction: float,
        mount_path: str,
        kv_prefix: str,
        options: VaultClientOptions | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not vault_addr.strip():
            raise ValueError("vault_addr must not be empty.")
        self._options = options or VaultClientOptions()
        self._mount_path = normalize_vault_path(mount_path, field_name="mount_path")
        self._kv_prefix = normalize_vault_path(kv_prefix, field_name="kv_prefix")
        self._owns_client = http_client is None
        self._client: httpx.AsyncClient | None = http_client or httpx.AsyncClient(
            base_url=vault_addr.rstrip("/"),
            timeout=httpx.Timeout(
                connect=self._options.connect_timeout_seconds,
                read=self._options.read_timeout_seconds,
                write=self._options.write_timeout_seconds,
                pool=self._options.pool_timeout_seconds,
            ),
            headers={"Content-Type": "application/json"},
        )
        self._session = _VaultTokenSession(
            self._client,
            username,
            password,
            token_refresh_after_lease_fraction,
            self._options,
        )

    def kv_path(self, reference: str) -> str:
        """Build the KV-v2 data URL after validating every path segment."""
        safe_reference = normalize_vault_path(reference, field_name="secret reference")
        return f"/v1/{self._mount_path}/data/{self._kv_prefix}/{safe_reference}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        secret_reference: str,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        """Send one authenticated request with bounded transient retries."""
        client = self._require_open_client()
        refreshed_rejected_token = False

        for attempt in range(1, self._options.request_max_attempts + 1):
            try:
                token = await self._session.ensure_token()
                if method == "GET":
                    response = await client.get(path, headers={"X-Vault-Token": token})
                elif method == "POST":
                    response = await client.post(
                        path,
                        headers={"X-Vault-Token": token},
                        json=json,
                    )
                else:
                    raise ValueError(f"Unsupported Vault HTTP method: {method!r}")
                if (
                    response.status_code in {401, 403}
                    and not refreshed_rejected_token
                    and attempt < self._options.request_max_attempts
                ):
                    # Tokens can be revoked before their advertised lease ends.
                    # Refresh once; a second denial is a real policy failure.
                    refreshed_rejected_token = True
                    self._session.invalidate()
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                return response
            except PermissionError:
                raise
            except _VaultLoginUnavailable as exc:
                cause = exc.__cause__
                reason = type(cause).__name__ if cause is not None else type(exc).__name__
                raise self._unavailable(secret_reference, reason) from cause
            except _VaultProtocolError as exc:
                raise self._unavailable(secret_reference, type(exc).__name__) from exc
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                    and exc.response.status_code != 429
                ):
                    raise
                if attempt >= self._options.request_max_attempts:
                    raise self._unavailable(secret_reference, self._safe_reason(exc)) from exc
                delay = self._options.retry_delay(attempt)
                logger.warning(
                    "Vault request failed; retrying",
                    extra={
                        "attempt": attempt,
                        "max_attempts": self._options.request_max_attempts,
                        "error_type": type(exc).__name__,
                        "retry_delay_seconds": round(delay, 3),
                    },
                )
                await asyncio.sleep(delay)

        raise self._unavailable(secret_reference, "retry budget exhausted")

    async def aclose(self) -> None:
        """Poison this gateway and close only an internally-owned client."""
        client = self._client
        self._client = None
        self._session.invalidate()
        if client is not None and self._owns_client:
            await client.aclose()

    def _require_open_client(self) -> httpx.AsyncClient:
        client = self._client
        if client is None:
            raise RuntimeError("Vault client is closed and cannot make requests.")
        return client

    @staticmethod
    def _safe_reason(exc: httpx.HTTPError) -> str:
        """Describe a failure without copying URLs, headers, or bodies."""
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        return type(exc).__name__

    @staticmethod
    def _unavailable(reference: str, reason: str) -> SecretBackendUnavailableError:
        return SecretBackendUnavailableError(
            backend_name="vault",
            secret_reference=reference,
            reason=reason,
        )


def normalize_vault_path(value: str, *, field_name: str) -> str:
    """Return a slash-normalized path containing only safe Vault segments."""
    normalized = value.strip("/")
    segments = normalized.split("/") if normalized else []
    if not segments or any(
        segment in {".", ".."} or _SAFE_PATH_SEGMENT.fullmatch(segment) is None
        for segment in segments
    ):
        raise ValueError(
            f"{field_name} must contain non-empty path segments using only letters, "
            "numbers, dots, underscores, or hyphens"
        )
    return "/".join(segments)
