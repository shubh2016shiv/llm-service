"""
app/providers/cloud/azure_openai_provider.py — Azure OpenAI Service provider.

Architecture
------------
    BaseProvider (ABC)
        └── AzureOpenAIProvider   ← chat, embed via Azure OpenAI REST API

Auth: api-key header (standard) or Entra ID token (via extra_config).
Transport: httpx.AsyncClient (shared, pooled).

Key differences from direct/openai_provider.py:
- Endpoint URL is Azure-specific: {endpoint}/openai/deployments/{deployment}/...
- Requires `api-key` header (not `Authorization: Bearer` by default).
- Optional `api-version` query parameter.
- Per-deployment model mapping (deployment name ≠ model name).
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


class AzureOpenAIProvider(BaseProvider[httpx.AsyncClient]):
    """Azure OpenAI Service provider.

    Thread-safe: all state is immutable settings + shared async HTTP client.
    """

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        headers = self._build_request_headers()
        payload = self._build_chat_payload(request)
        url = self._build_url("chat/completions")
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                url,
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
        url = self._build_url("chat/completions")
        t0 = time.monotonic()
        try:
            async with self._http_client.stream(
                "POST",
                url,
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
            "input": request.input if isinstance(request.input, list) else [request.input],
        }
        url = self._build_url("embeddings")
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                url,
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
            message="Rerank is not supported by Azure OpenAI.",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        from app.schemas.responses_schema import HealthStatus

        t0 = time.monotonic()
        try:
            response = await self._http_client.get(
                self._build_url(""),
                headers=self._build_request_headers(),
                timeout=self._effective_timeout(),
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(
                provider_name=self._static.provider_name,
                healthy=response.status_code < 500,
                latency_ms=latency_ms,
                detail=None if response.status_code < 500 else f"HTTP {response.status_code}",
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
        """Azure uses `api-key` header by default, not Bearer Authorization."""
        headers: dict[str, str] = {
            "api-key": self._api_key.get_secret_value(),
            "Content-Type": "application/json",
        }
        headers.update(self._deployment.extra_headers)
        return headers

    def _build_url(self, path: str) -> str:
        """Build the Azure OpenAI endpoint URL.

        Format: {endpoint}/openai/deployments/{deployment_key}/{path}?api-version=...
        """
        base = self._deployment.api_endpoint_url.rstrip("/")
        deployment_key = self._deployment.deployment_key
        url = f"{base}/openai/deployments/{deployment_key}"
        if path:
            url = f"{url}/{path.lstrip('/')}"
        # Append api-version query param if configured
        api_version = self._deployment.extra_config.get("api_version") or "2024-02-15-preview"
        return f"{url}?api-version={api_version}"

    def _build_chat_payload(self, request: ChatRequest) -> dict[str, object]:
        payload: dict[str, object] = {
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
            content=delta.get("content", "") or "",  # type: ignore[arg-type]
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
            embeddings=embeddings,  # type: ignore[arg-type]
            model=data.get("model", ""),  # type: ignore[arg-type]
            usage=usage,
        )
