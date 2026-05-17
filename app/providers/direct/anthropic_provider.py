"""
app/providers/direct/anthropic_provider.py — Anthropic Messages API provider.

Architecture
------------
    BaseProvider (ABC)
        └── AnthropicProvider   ← chat via /v1/messages

Auth: x-api-key header + anthropic-version header.
Transport: httpx.AsyncClient (shared, pooled).

Key differences from OpenAI:
- Uses Anthropic-specific Messages API (different endpoint + payload schema).
- Requires `anthropic-version` header on every request.
- Rerank / Embed are not natively supported — delegates gracefully.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import httpx

from app.providers.base_provider import BaseProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.schemas.requests import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )


class AnthropicProvider(BaseProvider[httpx.AsyncClient]):
    """Anthropic Messages API provider.

    Thread-safe: all state is immutable settings + shared async HTTP client.
    """

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        headers = self._build_request_headers()
        payload = self._build_messages_payload(request)
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                f"{self._deployment.api_endpoint_url}/messages",
                headers=headers,
                json=payload,
                timeout=self._effective_timeout(),
            )
            response.raise_for_status()
            latency_ms = int((time.monotonic() - t0) * 1000)
            data = response.json()
            self._emit_structured_log(
                "chat.generate",
                latency_ms,
                status_code=response.status_code,
                usage=data.get("usage"),
            )
            return self._parse_messages_response(data)
        except httpx.HTTPStatusError as exc:
            raise self._handle_provider_error(exc) from exc

    async def _stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        headers = self._build_request_headers()
        payload = self._build_messages_payload(request)
        payload["stream"] = True
        t0 = time.monotonic()
        try:
            async with self._http_client.stream(
                "POST",
                f"{self._deployment.api_endpoint_url}/messages",
                headers=headers,
                json=payload,
                timeout=self._effective_timeout(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        chunk_data = line.removeprefix("data: ")
                        yield self._parse_stream_event(json.loads(chunk_data))
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("chat.stream_generate", latency_ms)
        except httpx.HTTPStatusError as exc:
            raise self._handle_provider_error(exc) from exc

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def _embed(self, request: EmbedRequest) -> EmbedResponse:
        from app.core.exceptions import ProviderError

        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Embed is not supported by Anthropic.",
        )

    # ------------------------------------------------------------------
    # Rerank
    # ------------------------------------------------------------------

    async def _rerank(self, request: RerankRequest) -> RerankResponse:
        from app.core.exceptions import ProviderError

        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Rerank is not natively supported by Anthropic.",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        from app.schemas.responses import HealthStatus

        t0 = time.monotonic()
        try:
            response = await self._http_client.get(
                f"{self._deployment.api_endpoint_url}/models",
                headers=self._build_request_headers(),
                timeout=self._effective_timeout(),
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(
                provider_name=self._static.provider_name,
                healthy=response.status_code == 200,
                latency_ms=latency_ms,
                detail=None if response.status_code == 200 else f"HTTP {response.status_code}",
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(
                provider_name=self._static.provider_name,
                healthy=False,
                latency_ms=latency_ms,
                detail=str(exc),
            )



    # ------------------------------------------------------------------
    # Request Builder Helpers
    # ------------------------------------------------------------------

    def _build_request_headers(self) -> dict[str, str]:
        """Anthropic uses x-api-key (not Bearer Authorization)."""
        headers: dict[str, str] = {
            "x-api-key": self._api_key.get_secret_value(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        headers.update(self._deployment.extra_headers)
        return headers

    def _build_messages_payload(self, request: ChatRequest) -> dict[str, object]:
        # Separate system prompt from conversation messages
        system_prompts = [m for m in request.messages if m.role == "system"]
        messages = [m.model_dump(mode="json") for m in request.messages if m.role != "system"]

        payload: dict[str, object] = {
            "model": self._deployment.model_name,
            "messages": messages,
            "max_tokens": request.max_tokens or self._deployment.default_max_tokens or 1024,
        }
        if system_prompts:
            payload["system"] = "\n".join(m.content for m in system_prompts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop_sequences"] = request.stop
        return payload

    # ------------------------------------------------------------------
    # Response Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_messages_response(data: dict[str, object]) -> ChatResponse:
        from app.schemas.responses import ChatResponse, Usage

        raw_content = data.get("content", [])
        content_blocks = raw_content if isinstance(raw_content, list) else []
        text = "".join(
            block["text"]  # type: ignore[index]
            for block in content_blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        usage_raw = data.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("input_tokens", 0),  # type: ignore[union-attr]
                completion_tokens=usage_raw.get("output_tokens", 0),  # type: ignore[union-attr]
                total_tokens=(usage_raw.get("input_tokens", 0) + usage_raw.get("output_tokens", 0)),  # type: ignore[union-attr]
            )
            if usage_raw
            else None
        )
        return ChatResponse(
            content=text,
            role="assistant",
            finish_reason=data.get("stop_reason"),  # type: ignore[arg-type]
            usage=usage,
            model=data.get("model", ""),  # type: ignore[arg-type]
            raw_response=data,
        )

    @staticmethod
    def _parse_stream_event(data: dict[str, object]) -> ChatStreamChunk:
        from app.schemas.responses import ChatStreamChunk

        event_type = data.get("type", "")
        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            return ChatStreamChunk(
                content=delta.get("text", "") or "",  # type: ignore[arg-type]
                finish_reason=None,
                index=data.get("index", 0),  # type: ignore[arg-type]
                raw_chunk=data,
            )
        if event_type == "message_stop":
            return ChatStreamChunk(
                content="",
                finish_reason="stop",
                index=0,
                raw_chunk=data,
            )
        return ChatStreamChunk(
            content="",
            finish_reason=None,
            index=0,
            raw_chunk=data,
        )
