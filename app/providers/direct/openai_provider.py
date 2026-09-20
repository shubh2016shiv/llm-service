"""
OpenAI Provider Adapter
=======================

Concrete adapter for OpenAI-compatible chat and embedding endpoints.

Why this module exists:
    - OpenAI payload/response structure is widely used as the baseline contract.
    - We still isolate implementation details (headers, SSE chunks, usage parsing)
      behind the shared provider interface so upstream code stays generic.

Rationale:
    - Streaming and non-streaming paths are explicit so failures and telemetry
      are observable per mode.
    - Provider-native payload is kept in responses for diagnostics while API-facing
      response contracts remain normalized.

Step-by-step call flow:
    1. Build provider-specific headers and payload.
    2. Perform HTTP call through shared client.
    3. Validate status and parse provider response.
    4. Emit structured telemetry with latency/usage.
    5. Return normalized schema response to service layer.

Author: Shubham Singh
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.core.exceptions import ProviderError
from app.providers.base_provider import BaseProvider
from app.schemas.responses_schema import (
    ChatResponse,
    ChatStreamChunk,
    EmbedResponse,
    HealthStatus,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import RerankResponse


class OpenAIProvider(BaseProvider[httpx.AsyncClient]):
    """OpenAI REST API provider (chat + embed).

    Thread-safe: all state is immutable settings + shared async HTTP client.
    Per-request variables (headers, payloads) are local to each call frame.

    Design intent:
        Keep OpenAI wire-format specifics here so upstream code can remain
        provider-agnostic and interact only with base-provider contracts.
    """

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        """Call OpenAI chat completions endpoint and normalize response."""
        headers = self._build_request_headers()
        payload = self._build_chat_payload(request)
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                f"{self._context.api_endpoint_url}/chat/completions",
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
            return self._parse_chat_response(data)
        except httpx.HTTPStatusError as exc:
            raise self._handle_provider_error(exc) from exc

    async def _stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Stream OpenAI chat completion chunks via SSE ``data:`` frames."""
        headers = self._build_request_headers()
        payload = self._build_chat_payload(request)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        t0 = time.monotonic()
        try:
            async with self._http_client.stream(
                "POST",
                f"{self._context.api_endpoint_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._effective_timeout(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        chunk_data = line.removeprefix("data: ")
                        if chunk_data == "[DONE]":
                            break
                        yield self._parse_stream_chunk(json.loads(chunk_data))
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("chat.stream_generate", latency_ms)
        except httpx.HTTPStatusError as exc:
            raise self._handle_provider_error(exc) from exc

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def _embed(self, request: EmbedRequest) -> EmbedResponse:
        """Call OpenAI embeddings endpoint and normalize embedding payload."""
        headers = self._build_request_headers()
        payload = self._build_embed_payload(request)
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                f"{self._context.api_endpoint_url}/embeddings",
                headers=headers,
                json=payload,
                timeout=self._effective_timeout(),
            )
            response.raise_for_status()
            latency_ms = int((time.monotonic() - t0) * 1000)
            data = response.json()
            self._emit_structured_log(
                "embed",
                latency_ms,
                status_code=response.status_code,
                usage=data.get("usage"),
            )
            return self._parse_embed_response(data)
        except httpx.HTTPStatusError as exc:
            raise self._handle_provider_error(exc) from exc

    # ------------------------------------------------------------------
    # Rerank
    # ------------------------------------------------------------------

    async def _rerank(self, request: RerankRequest) -> RerankResponse:
        # OpenAI does not natively support rerank — delegate to a compatible
        # model or raise a ProviderError.
        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Rerank is not supported by OpenAI.",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """Probe OpenAI models endpoint as lightweight availability check."""
        t0 = time.monotonic()
        try:
            response = await self._http_client.get(
                f"{self._context.api_endpoint_url}/models",
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
                detail=self._safe_health_error_detail(exc),
            )

    # ------------------------------------------------------------------
    # Request Builder Helpers
    # ------------------------------------------------------------------

    def _build_request_headers(self) -> dict[str, str]:
        """Build OpenAI auth/content headers plus resolved extra headers."""
        headers = self._build_auth_headers()
        headers["Content-Type"] = "application/json"
        headers.update(self._context.extra_headers)
        return headers

    def _build_chat_payload(self, request: ChatRequest) -> dict[str, object]:
        """Translate domain chat request into OpenAI chat-completions payload."""
        payload: dict[str, object] = {
            "model": self._context.model_name,
            "messages": [m.model_dump(mode="json") for m in request.messages],
        }
        payload["temperature"] = (
            request.temperature
            if request.temperature is not None
            else self._context.effective_temperature
        )
        payload["max_tokens"] = (
            request.max_tokens
            if request.max_tokens is not None
            else self._context.effective_max_tokens
        )
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = request.stop
        return payload

    def _build_embed_payload(self, request: EmbedRequest) -> dict[str, object]:
        """Translate domain embed request into OpenAI embeddings payload."""
        return {
            "model": self._context.model_name,
            "input": request.input if isinstance(request.input, list) else [request.input],
        }

    # ------------------------------------------------------------------
    # Response Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_chat_response(data: dict[str, Any]) -> ChatResponse:
        """Parse OpenAI chat response JSON into normalized ``ChatResponse``."""
        # Any here is confined to this JSON-response boundary: every value
        # is validated when the ChatResponse below is constructed.
        choice = data["choices"][0]
        message = choice["message"]
        usage_raw = data.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )
            if usage_raw
            else None
        )
        return ChatResponse(
            content=message["content"],
            role=message["role"],
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            model=data.get("model", ""),
            raw_response=data,
        )

    @staticmethod
    def _parse_stream_chunk(data: dict[str, Any]) -> ChatStreamChunk:
        """Parse one OpenAI stream chunk event into ``ChatStreamChunk``."""
        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        delta = choice.get("delta", {})
        usage_raw = data.get("usage") or {}
        usage = Usage(**usage_raw) if usage_raw else None
        return ChatStreamChunk(
            content=delta.get("content", "") or "",
            finish_reason=choice.get("finish_reason"),
            index=choice.get("index", 0),
            usage=usage,
            raw_chunk=data,
        )

    @staticmethod
    def _parse_embed_response(data: dict[str, Any]) -> EmbedResponse:
        """Parse OpenAI embeddings response into normalized ``EmbedResponse``."""
        embeddings = [item["embedding"] for item in data["data"]]
        usage_raw = data.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )
            if usage_raw
            else None
        )
        return EmbedResponse(
            embeddings=embeddings,
            model=data.get("model", ""),
            usage=usage,
        )
