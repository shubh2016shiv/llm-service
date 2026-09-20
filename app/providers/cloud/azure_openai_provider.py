"""
Azure OpenAI Provider Adapter
=============================

Concrete adapter for Azure OpenAI deployment-based endpoints.

Why this module exists:
    - Azure OpenAI routes through deployment names and Azure-specific URL structure.
    - Header and versioning conventions differ from direct OpenAI API usage.

Rationale:
    - Keeping Azure path/query/auth details localized prevents leakage of
      platform-specific rules into shared inference services.

Step-by-step call flow:
    1. Build Azure-specific headers and deployment-scoped URL.
    2. Build payload from domain request.
    3. Execute request via shared HTTP client.
    4. Parse Azure response format into normalized schemas.
    5. Emit structured telemetry with latency/usage.

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


class AzureOpenAIProvider(BaseProvider[httpx.AsyncClient]):
    """Azure OpenAI Service provider.

    Thread-safe: all state is immutable settings + shared async HTTP client.

    Design intent:
        Keep Azure deployment naming and API-version nuances in one place so
        the rest of the stack can treat Azure like any other provider adapter.
    """

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        """Call Azure OpenAI chat completions endpoint and normalize response."""
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
        """Stream Azure OpenAI SSE chunks into unified stream chunk schema."""
        headers = self._build_request_headers()
        payload = self._build_chat_payload(request)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
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
        """Call Azure OpenAI embeddings endpoint and normalize response payload."""
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
        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Rerank is not supported by Azure OpenAI.",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """Run lightweight Azure endpoint health probe."""
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
                detail=self._safe_health_error_detail(exc),
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
        headers.update(self._context.extra_headers)
        return headers

    def _build_url(self, path: str) -> str:
        """Build the Azure OpenAI endpoint URL.

        Format: {endpoint}/openai/deployments/{azure_deployment_name}/{path}?api-version=...

        Azure deployment name priority:
          1. extra_config["azure_deployment_name"] — explicit Azure model deployment name
          2. deployment_key — authorized tenant routing key as fallback
        """
        base = self._context.api_endpoint_url.rstrip("/")
        azure_deployment_name = (
            str(self._context.extra_config["azure_deployment_name"])
            if "azure_deployment_name" in self._context.extra_config
            else self._context.deployment_key
        )
        url = f"{base}/openai/deployments/{azure_deployment_name}"
        if path:
            url = f"{url}/{path.lstrip('/')}"
        api_version = self._context.extra_config.get("api_version") or "2024-02-15-preview"
        return f"{url}?api-version={api_version}"

    def _build_chat_payload(self, request: ChatRequest) -> dict[str, object]:
        """Translate domain chat request into Azure OpenAI payload fields."""
        payload: dict[str, object] = {
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

    # ------------------------------------------------------------------
    # Response Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_chat_response(data: dict[str, Any]) -> ChatResponse:
        """Parse Azure chat response into normalized ``ChatResponse``."""
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
        """Parse one Azure stream event into normalized stream chunk."""
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
        """Parse Azure embeddings response into normalized ``EmbedResponse``."""
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
