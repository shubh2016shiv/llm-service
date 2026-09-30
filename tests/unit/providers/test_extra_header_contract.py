"""Configured provider headers cannot replace adapter credentials."""

from __future__ import annotations

import httpx
import pytest
from aiobreaker import CircuitBreaker
from pydantic import SecretStr

from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.direct.anthropic_provider import AnthropicProvider
from app.providers.direct.openai_provider import OpenAIProvider
from app.providers.direct.vllm_provider import VLLMProvider
from tests.unit.providers.test_provider_registry import build_route


@pytest.mark.parametrize(
    "provider", [OpenAIProvider, AzureOpenAIProvider, AnthropicProvider, VLLMProvider]
)
@pytest.mark.parametrize("header", ["aUtHoRiZaTiOn", "API-Key", "x-API-key", "Host"])
def test_configured_reserved_header_is_rejected(provider: type, header: str) -> None:
    """REQ: untrusted route headers cannot alter provider authentication or destination."""
    route = build_route("a" * 64).model_copy(update={"extra_headers": {header: "attacker"}})
    adapter = provider(route, httpx.AsyncClient(), CircuitBreaker(), SecretStr("stored-key"))

    with pytest.raises(ValueError, match="reserved header"):
        adapter._build_request_headers()


@pytest.mark.parametrize(
    "provider", [OpenAIProvider, AzureOpenAIProvider, AnthropicProvider, VLLMProvider]
)
def test_configured_non_reserved_header_is_preserved(provider: type) -> None:
    """REQ: approved custom headers remain available to upstream gateways."""
    route = build_route("a" * 64).model_copy(update={"extra_headers": {"x-trace-tag": "test"}})
    adapter = provider(route, httpx.AsyncClient(), CircuitBreaker(), SecretStr("stored-key"))

    assert adapter._build_request_headers()["x-trace-tag"] == "test"
