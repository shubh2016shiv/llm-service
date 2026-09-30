"""OpenAI-compatible embedding responses retain input order and cardinality."""

from __future__ import annotations

import pytest

from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.direct.openai_provider import OpenAIProvider
from app.providers.direct.vllm_provider import VLLMProvider


@pytest.mark.parametrize("provider", [OpenAIProvider, AzureOpenAIProvider, VLLMProvider])
def test_embed_response_out_of_order_indexes_returns_input_order(provider: type) -> None:
    """REQ: each vector belongs to the input at its provider-reported index."""
    data = {
        "data": [
            {"index": 1, "embedding": [2.0]},
            {"index": 0, "embedding": [1.0]},
        ]
    }

    result = provider._parse_embed_response(data, expected_count=2)

    assert result.embeddings == [[1.0], [2.0]]


@pytest.mark.parametrize("provider", [OpenAIProvider, AzureOpenAIProvider, VLLMProvider])
@pytest.mark.parametrize(
    "entries",
    [
        [{"index": 0, "embedding": [1.0]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 2, "embedding": [2.0]}],
    ],
)
def test_embed_response_missing_or_duplicate_indexes_fails(provider: type, entries: list) -> None:
    """REQ: incomplete or ambiguous batches cannot be returned as successful results."""
    with pytest.raises(ValueError, match=r"embeddings|indexes"):
        provider._parse_embed_response({"data": entries}, expected_count=2)
