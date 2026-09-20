"""
AWS Bedrock Provider Adapter
============================

Concrete adapter for AWS Bedrock runtime operations.

Why this module exists:
    - Bedrock uses AWS SDK semantics and IAM credential resolution rather than
      direct API-key HTTP flows.
    - Request/response payloads differ from OpenAI-style contracts and must be
      translated explicitly.

Rationale:
    - Bedrock transport is intentionally separated from httpx providers so
      SDK-specific lifecycle, auth, and error behavior stay encapsulated.

Step-by-step call flow:
    1. Build Bedrock-native payload from domain request.
    2. Create short-lived Bedrock runtime client from session.
    3. Invoke converse/invoke_model operation.
    4. Parse AWS response objects into normalized schemas.
    5. Emit structured telemetry.

Author: Shubham Singh
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast

from botocore.config import Config

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

    import aiobreaker
    from pydantic import SecretStr

    from app.inference_routing.models import ResolvedRoute
    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import RerankResponse


class BedrockProvider(BaseProvider[object]):
    """AWS Bedrock runtime provider.

    Thread-safe: boto3 sessions are thread-safe. No per-request mutable state.

    Overrides __init__ to accept an aioboto3 session instead of httpx.AsyncClient
    (per implementation_plan.md §Q2: Bedrock uses its own SDK transport).

    Design intent:
        Keep AWS SDK invocation details isolated so service layers interact with
        the same base-provider contract as REST-based providers.
    """

    def __init__(
        self,
        context: ResolvedRoute,
        http_client: object,  # aioboto3.Session in practice; typed loosely for ABC compatibility
        circuit_breaker: aiobreaker.CircuitBreaker,
        api_key: SecretStr | None = None,  # Accepted for registry compat; Bedrock uses IAM auth
    ) -> None:
        super().__init__(context, http_client, circuit_breaker, api_key)
        # aioboto3 ships no type stubs, so the session is typed Any here:
        # the client(...) calls below then type-check without ignores.
        self._bedrock_session: Any = http_client  # stored as the aioboto3 session

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        """Invoke Bedrock Converse API and normalize chat response."""
        payload = self._build_converse_payload(request)
        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
                config=self._client_config(),
            ) as client:
                response = await client.converse(**payload)
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log(
                "chat.generate",
                latency_ms,
                status_code=200,
                usage=response.get("usage"),
            )
            return self._parse_converse_response(response)
        except Exception as exc:
            raise self._handle_provider_error(exc) from exc

    async def _stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Invoke Bedrock Converse stream API and yield normalized chunks."""
        payload = self._build_converse_stream_payload(request)
        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
                config=self._client_config(),
            ) as client:
                stream_response = await client.converse_stream(**payload)
                stream = stream_response.get("stream")
                if stream:
                    async for event in stream:
                        yield self._parse_converse_stream_event(event)
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("chat.stream_generate", latency_ms)
        except Exception as exc:
            raise self._handle_provider_error(exc) from exc

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def _embed(self, request: EmbedRequest) -> EmbedResponse:
        """Invoke Bedrock model endpoint for embeddings and normalize output."""
        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
                config=self._client_config(),
            ) as client:
                # Bedrock uses InvokeModel for embeddings (pre-Converse API)
                body = self._build_embed_body(request)
                response = await client.invoke_model(
                    modelId=self._context.model_name,
                    body=json.dumps(body),
                    contentType="application/json",
                )
                response_body = json.loads(await response["body"].read())
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("embed", latency_ms)
            return EmbedResponse(
                embeddings=self._extract_embeddings(response_body),
                model=self._context.model_name,
                usage=Usage(),
            )
        except Exception as exc:
            raise self._handle_provider_error(exc) from exc

    # ------------------------------------------------------------------
    # Rerank
    # ------------------------------------------------------------------

    async def _rerank(self, request: RerankRequest) -> RerankResponse:
        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Rerank is not supported by Bedrock (use Cohere via Bedrock marketplace if needed).",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """Use Bedrock foundation-model listing as a health/permission probe."""
        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(
                "bedrock",
                region_name=self._resolve_aws_region(),
                config=self._client_config(),
            ) as client:
                # Lightweight check: just verify the client can connect
                await client.list_foundation_models()
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(
                provider_name=self._static.provider_name,
                healthy=True,
                latency_ms=latency_ms,
                detail=None,
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
    # Payload Builders (Bedrock Converse API)
    # ------------------------------------------------------------------

    def _build_converse_payload(self, request: ChatRequest) -> dict[str, object]:
        """Build Bedrock Converse request payload from domain chat request."""
        return {
            "modelId": self._context.model_name,
            "messages": self._convert_messages_to_bedrock(request),
            "inferenceConfig": {
                "temperature": (
                    request.temperature
                    if request.temperature is not None
                    else self._context.effective_temperature
                ),
                "maxTokens": (
                    request.max_tokens
                    if request.max_tokens is not None
                    else self._context.effective_max_tokens
                ),
            },
        }

    def _build_converse_stream_payload(self, request: ChatRequest) -> dict[str, object]:
        """Build Bedrock Converse stream payload (currently same base fields)."""
        payload = self._build_converse_payload(request)
        inference_config = cast("dict[str, object]", payload.get("inferenceConfig", {}))
        payload["inferenceConfig"] = {**inference_config}
        return payload

    def _build_embed_body(self, request: EmbedRequest) -> dict[str, object]:
        """Build Bedrock embedding invoke-model body from domain embed request."""
        input_text = request.input if isinstance(request.input, str) else request.input[0]
        return {"inputText": input_text}

    @staticmethod
    def _convert_messages_to_bedrock(request: ChatRequest) -> list[dict[str, object]]:
        """Convert our domain ChatMessage list into Bedrock Converse format."""
        messages: list[dict[str, object]] = []
        for msg in request.messages:
            if msg.role == "system":
                # System prompts are handled separately in Converse API via `system` param
                continue
            messages.append(
                {
                    "role": msg.role,
                    "content": [{"text": msg.content}],
                }
            )
        return messages

    # ------------------------------------------------------------------
    # Response Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_converse_response(response: dict[str, Any]) -> ChatResponse:
        """Parse Bedrock Converse response into normalized ``ChatResponse``."""
        # Any here is confined to this JSON-response boundary: every value
        # is validated when the ChatResponse below is constructed.
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        text = "".join(block.get("text", "") for block in content_blocks)
        usage_raw = response.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("inputTokens", 0),
                completion_tokens=usage_raw.get("outputTokens", 0),
                total_tokens=usage_raw.get("totalTokens", 0),
            )
            if usage_raw
            else None
        )
        return ChatResponse(
            content=text,
            role=message.get("role", "assistant"),
            finish_reason=response.get("stopReason"),
            usage=usage,
            model=response.get("modelId", ""),
            raw_response=response,
        )

    @staticmethod
    def _parse_converse_stream_event(event: dict[str, Any]) -> ChatStreamChunk:
        """Parse Bedrock stream event object into normalized stream chunk."""
        content = ""
        if "contentBlockDelta" in event:
            content = event["contentBlockDelta"].get("delta", {}).get("text", "") or ""

        finish_reason = None
        if "messageStop" in event:
            finish_reason = event["messageStop"].get("stopReason")

        usage_raw = event.get("metadata", {}).get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("inputTokens", 0),
                completion_tokens=usage_raw.get("outputTokens", 0),
                total_tokens=usage_raw.get("totalTokens", 0),
            )
            if usage_raw
            else None
        )

        return ChatStreamChunk(
            content=content,
            finish_reason=finish_reason,
            index=0,
            usage=usage,
            raw_chunk=event,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_aws_region(self) -> str:
        """Resolve AWS region from resolved context with safe fallback.

        Resolution order:
            1. ``context.cloud_region`` (explicit route-level value)
            2. ``context.extra_config['aws_region']`` (provider-specific override)
            3. hard default ``us-east-1``
        """
        if self._context.cloud_region:
            return self._context.cloud_region
        value = self._context.extra_config.get("aws_region")
        if isinstance(value, str) and value:
            return value
        return "us-east-1"

    def _client_config(self) -> Config:
        """Apply the resolved timeout and disable hidden SDK retries per call."""
        timeout_seconds = self._effective_timeout()
        return Config(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"max_attempts": 0, "mode": "standard"},
        )

    @staticmethod
    def _extract_embeddings(response: dict[str, object]) -> list[list[float]]:
        """Normalize Titan's single vector and Cohere's vector collection."""
        vectors = response.get("embeddings")
        if isinstance(vectors, list) and all(isinstance(item, list) for item in vectors):
            return cast("list[list[float]]", vectors)
        vector = response.get("embedding")
        if isinstance(vector, list):
            return [cast("list[float]", vector)]
        raise ValueError("Bedrock embedding response did not contain a vector.")
