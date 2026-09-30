"""Provider streams must prove completion before the service finalizes quota.

Architecture:
    controlled provider transport -> real adapter -> normalized terminal outcome
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
import pytest
from aiobreaker import CircuitBreaker
from pydantic import SecretStr

from app.core.exceptions import ProviderInternalError
from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.cloud.bedrock_provider import BedrockProvider
from app.providers.direct.anthropic_provider import AnthropicProvider
from app.providers.direct.openai_provider import OpenAIProvider
from app.providers.direct.vllm_provider import VLLMProvider
from app.schemas.requests_schema import ChatMessage, ChatRequest, EmbedRequest
from tests.unit.providers.test_provider_registry import build_route

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


REQUEST = ChatRequest(
    thread_id=UUID("70000000-0000-0000-0000-000000000001"),
    messages=[ChatMessage(role="user", content="hello")],
    stream=True,
)


class BodyTransport(httpx.AsyncBaseTransport):
    """Serve one controlled SSE response to the real HTTP adapter."""

    def __init__(self, body: str) -> None:
        self.body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=self.body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "terminal"),
    [
        (OpenAIProvider, "data: [DONE]\n\n"),
        (VLLMProvider, "data: [DONE]\n\n"),
        (AzureOpenAIProvider, "data: [DONE]\n\n"),
        (AnthropicProvider, 'data: {"type":"message_stop"}\n\n'),
    ],
)
async def test_http_adapter_rejects_truncated_stream_and_accepts_terminal_marker(
    adapter_type,
    terminal: str,
) -> None:
    """REQ: a clean TCP close without protocol completion is a failed request."""
    body = (
        'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n\n'
        if adapter_type is not AnthropicProvider
        else 'data: {"type":"content_block_delta","index":0,"delta":{"text":"hello"}}\n\n'
    )
    for suffix, expected_success in (("", False), (terminal, True)):
        async with httpx.AsyncClient(
            transport=BodyTransport(body + suffix),
        ) as client:
            adapter = adapter_type(
                build_route("a" * 64), client, CircuitBreaker(), SecretStr("key")
            )
            if expected_success:
                chunks = [chunk async for chunk in adapter._stream_generate(REQUEST)]
                assert chunks[0].content == "hello"
            else:
                with pytest.raises(ProviderInternalError, match="completion marker"):
                    _ = [chunk async for chunk in adapter._stream_generate(REQUEST)]


class FakeBedrockSession:
    """Return one finite Converse event sequence without AWS network access."""

    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events

    def client(self, service_name: str, *, region_name: str, config: object):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def converse_stream(
        self, **payload: object
    ) -> dict[str, AsyncIterator[dict[str, object]]]:
        async def events() -> AsyncIterator[dict[str, object]]:
            for event in self.events:
                yield event

        return {"stream": events()}


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_bedrock_requires_message_stop_before_success(complete: bool) -> None:
    """REQ: a Bedrock stream closed before messageStop is a failed request."""
    events: list[dict[str, object]] = [
        {"contentBlockDelta": {"delta": {"text": "hello"}}},
    ]
    if complete:
        events.append({"messageStop": {"stopReason": "end_turn"}})
    adapter = BedrockProvider(build_route("a" * 64), FakeBedrockSession(events), CircuitBreaker())

    if complete:
        chunks = [chunk async for chunk in adapter._stream_generate(REQUEST)]
        assert chunks[0].content == "hello"
    else:
        with pytest.raises(ProviderInternalError, match="completion marker"):
            _ = [chunk async for chunk in adapter._stream_generate(REQUEST)]


def test_bedrock_converse_payload_keeps_system_messages() -> None:
    """System instructions use Converse's separate system field in both modes."""
    adapter = BedrockProvider(build_route("a" * 64), FakeBedrockSession([]), CircuitBreaker())
    request = REQUEST.model_copy(
        update={
            "messages": [
                ChatMessage(role="system", content="Follow policy"),
                ChatMessage(role="user", content="hello"),
            ]
        }
    )

    for payload in (
        adapter._build_converse_payload(request),
        adapter._build_converse_stream_payload(request),
    ):
        assert payload["system"] == [{"text": "Follow policy"}]
        assert payload["messages"] == [{"role": "user", "content": [{"text": "hello"}]}]


def test_bedrock_converse_payload_keeps_sampling_and_stop_controls() -> None:
    """REQ: supported chat controls reach Converse in both response modes."""
    adapter = BedrockProvider(build_route("a" * 64), FakeBedrockSession([]), CircuitBreaker())
    request = REQUEST.model_copy(update={"top_p": 0.6, "stop": ["END"]})

    for payload in (
        adapter._build_converse_payload(request),
        adapter._build_converse_stream_payload(request),
    ):
        assert payload["inferenceConfig"]["topP"] == 0.6
        assert payload["inferenceConfig"]["stopSequences"] == ["END"]


class FakeEmbedBody:
    """Return one native embedding payload from the SDK response body."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class FakeEmbedBedrockSession(FakeBedrockSession):
    """Record each InvokeModel request and return one vector per input."""

    def __init__(self, *, include_usage: bool) -> None:
        super().__init__([])
        self.include_usage = include_usage
        self.inputs: list[str] = []

    async def invoke_model(self, **payload: object) -> dict[str, FakeEmbedBody]:
        input_text = json.loads(str(payload["body"]))["inputText"]
        self.inputs.append(input_text)
        response: dict[str, object] = {"embedding": [float(len(self.inputs)), 0.5]}
        if self.include_usage:
            response["inputTextTokenCount"] = len(self.inputs)
        return {"body": FakeEmbedBody(response)}


@pytest.mark.asyncio
@pytest.mark.parametrize("include_usage", [False, True])
async def test_bedrock_embedding_batch_preserves_cardinality_and_usage(
    include_usage: bool,
) -> None:
    """Every requested input gets a vector; absent usage is never reported as zero."""
    session = FakeEmbedBedrockSession(include_usage=include_usage)
    adapter = BedrockProvider(build_route("a" * 64), session, CircuitBreaker())

    result = await adapter._embed(EmbedRequest(input=["one", "two"]))

    assert session.inputs == ["one", "two"]
    assert result.embeddings == [[1.0, 0.5], [2.0, 0.5]]
    assert (result.usage.prompt_tokens if result.usage else None) == (3 if include_usage else None)
