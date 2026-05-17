"""
app/providers/direct/vllm_provider.py — vLLM (OpenAI-compatible) self-hosted provider.

Architecture
------------
    BaseProvider (ABC)
        └── VLLMProvider   ← chat, embed via OpenAI-compatible /v1 endpoints

Auth: Optional API key (Bearer token) — vLLM can run with or without auth.
Transport: httpx.AsyncClient (shared, pooled).

Design note:
    vLLM exposes an OpenAI-compatible REST API, so this provider is a
    lightweight variant of OpenAIProvider. It strips features not supported
    by vLLM (e.g. logprobs, function calling, some params) and uses the
    configured deployment endpoint directly.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import httpx

from app.providers.base_provider import BaseProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )


class VLLMProvider(BaseProvider[httpx.AsyncClient]):
    """vLLM OpenAI-compatible self-hosted provider.

    Thread-safe: all state is immutable settings + shared async HTTP client.
    """

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        headers = self._build_request_headers()
        payload = self._build_chat_payload(request)
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                f"{self._deployment.api_endpoint_url}/chat/completions",
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
        headers = self._build_request_headers()
        payload = self._build_chat_payload(request)
        payload["stream"] = True
        t0 = time.monotonic()
        try:
            async with self._http_client.stream(
                "POST",
                f"{self._deployment.api_endpoint_url}/chat/completions",
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
        headers = self._build_request_headers()
        payload = {
            "model": self._deployment.model_name,
            "input": request.input if isinstance(request.input, list) else [request.input],
        }
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                f"{self._deployment.api_endpoint_url}/embeddings",
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
        from app.core.exceptions import ProviderError

        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Rerank is not supported by vLLM.",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        from app.schemas.responses_schema import HealthStatus

        t0 = time.monotonic()
        try:
            response = await self._http_client.get(
                f"{self._deployment.api_endpoint_url}/health",
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
        """vLLM may run without auth. Only set Authorization if an API key is configured."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        api_key = self._api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(self._deployment.extra_headers)
        return headers

    def _build_chat_payload(self, request: ChatRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._deployment.model_name,
            "messages": [m.model_dump(mode="json") for m in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = request.stop
        return payload

    # ------------------------------------------------------------------
    # Response Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_chat_response(data: dict[str, object]) -> ChatResponse:
        from app.schemas.responses_schema import ChatResponse, Usage

        choice = data["choices"][0]  # type: ignore[index]
        message = choice["message"]  # type: ignore[index]
        usage_raw = data.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),  # type: ignore[union-attr]
                completion_tokens=usage_raw.get("completion_tokens", 0),  # type: ignore[union-attr]
                total_tokens=usage_raw.get("total_tokens", 0),  # type: ignore[union-attr]
            )
            if usage_raw
            else None
        )
        return ChatResponse(
            content=message["content"],  # type: ignore[index]
            role=message["role"],  # type: ignore[index]
            finish_reason=choice.get("finish_reason"),  # type: ignore[index]
            usage=usage,
            model=data.get("model", ""),  # type: ignore[arg-type]
            raw_response=data,
        )

    @staticmethod
    def _parse_stream_chunk(data: dict[str, object]) -> ChatStreamChunk:
        from app.schemas.responses_schema import ChatStreamChunk

        delta = data["choices"][0].get("delta", {})  # type: ignore[index]
        return ChatStreamChunk(
            content=delta.get("content", "") or "",
            finish_reason=data["choices"][0].get("finish_reason"),  # type: ignore[index]
            index=data["choices"][0].get("index", 0),  # type: ignore[index]
            raw_chunk=data,
        )

    @staticmethod
    def _parse_embed_response(data: dict[str, object]) -> EmbedResponse:
        from app.schemas.responses_schema import EmbedResponse, Usage

        embeddings = [item["embedding"] for item in data["data"]]  # type: ignore[index]
        usage_raw = data.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),  # type: ignore[union-attr]
                total_tokens=usage_raw.get("total_tokens", 0),  # type: ignore[union-attr]
            )
            if usage_raw
            else None
        )
        return EmbedResponse(
            embeddings=embeddings,
            model=data.get("model", ""),  # type: ignore[arg-type]
            usage=usage,
        )
