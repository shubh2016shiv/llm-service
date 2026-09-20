"""Tests for the environment-variable secret backend."""

from __future__ import annotations

import pytest

from app.adapters.secret_management import EnvironmentSecretStore


@pytest.mark.asyncio
async def test_get_secret_reads_environment_variable(monkeypatch) -> None:
    """A set variable is returned as the plaintext secret."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    secret = await EnvironmentSecretStore().get_secret("OPENAI_API_KEY", tenant_id="tenant-a")

    assert secret == "sk-test"


@pytest.mark.asyncio
async def test_get_secret_raises_helpful_key_error_when_variable_missing(
    monkeypatch,
) -> None:
    """A missing variable raises KeyError with fix-it guidance."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(KeyError, match="is not set"):
        await EnvironmentSecretStore().get_secret("OPENAI_API_KEY", tenant_id="tenant-a")


@pytest.mark.asyncio
async def test_get_secret_rejects_empty_environment_value(monkeypatch) -> None:
    """An empty variable is configuration damage, not a usable credential."""
    monkeypatch.setenv("OPENAI_API_KEY", "")

    with pytest.raises(ValueError, match="set but empty"):
        await EnvironmentSecretStore().get_secret("OPENAI_API_KEY", tenant_id="tenant-a")
