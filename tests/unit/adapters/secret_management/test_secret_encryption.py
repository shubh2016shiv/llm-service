"""Tests for tenant-scoped provider credential encryption."""

from __future__ import annotations

import base64

import pytest

from app.adapters.secret_management import AesGcmSecretStore
from app.adapters.secret_management.tenant_secret_encryption import (
    encrypt_api_key,
)

MASTER_KEY = b"m" * 32
MASTER_KEY_BASE64 = base64.b64encode(MASTER_KEY).decode("ascii")


@pytest.mark.asyncio
async def test_secret_store_decrypts_registered_tenant_ciphertext() -> None:
    """A registered payload round-trips only with its tenant-derived key."""
    ciphertext = encrypt_api_key(
        "provider-secret",
        MASTER_KEY,
        "tenant-a",
        secret_reference="openai/default",
    )
    store = AesGcmSecretStore(
        MASTER_KEY_BASE64,
        {("tenant-a", "openai/default"): ciphertext},
    )

    plaintext = await store.get_secret("openai/default", tenant_id="tenant-a")

    assert plaintext == "provider-secret"


def test_secret_store_rejects_malformed_master_key_base64() -> None:
    """Malformed key material fails at startup instead of during a provider call."""
    with pytest.raises(ValueError, match="valid base64"):
        AesGcmSecretStore("%%%not-base64%%%")


@pytest.mark.asyncio
async def test_secret_store_cannot_see_another_tenants_reference() -> None:
    """Tenant-scoped registry lookup prevents cross-tenant credential access."""
    ciphertext = encrypt_api_key(
        "provider-secret",
        MASTER_KEY,
        "tenant-a",
        secret_reference="openai/default",
    )
    store = AesGcmSecretStore(
        MASTER_KEY_BASE64,
        {("tenant-a", "openai/default"): ciphertext},
    )

    with pytest.raises(KeyError, match="not found"):
        await store.get_secret("openai/default", tenant_id="tenant-b")


def test_ciphertext_cannot_be_moved_to_another_reference() -> None:
    """Authenticated metadata prevents a valid blob being relabelled in storage."""
    ciphertext = encrypt_api_key(
        "provider-secret",
        MASTER_KEY,
        "tenant-a",
        secret_reference="openai/original",
    )
    with pytest.raises(ValueError, match="Encrypted secret is invalid"):
        AesGcmSecretStore(
            MASTER_KEY_BASE64,
            {("tenant-a", "openai/relabelled"): ciphertext},
        )
