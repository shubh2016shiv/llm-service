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
from typing import TYPE_CHECKING

from app.providers.base_provider import BaseProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import aiobreaker
    from pydantic import SecretStr

    from app.inference_routing.models import ResolvedExecutionContext
    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )


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
        context: ResolvedExecutionContext,
        http_client: object,  # aioboto3.Session in practice; typed loosely for ABC compatibility
        circuit_breaker: aiobreaker.CircuitBreaker,
        api_key: SecretStr | None = None,  # Accepted for registry compat; Bedrock uses IAM auth
    ) -> None:
        super().__init__(context, http_client, circuit_breaker, api_key)
        self._bedrock_session = http_client  # stored as the aioboto3 session

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        """Invoke Bedrock Converse API and normalize chat response."""
        payload = self._build_converse_payload(request)
        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(  # type: ignore[union-attr]
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
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
            async with self._bedrock_session.client(  # type: ignore[union-attr]
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
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
        from app.schemas.responses_schema import EmbedResponse, Usage

        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(  # type: ignore[union-attr]
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
            ) as client:
                # Bedrock uses InvokeModel for embeddings (pre-Converse API)
                body = self._build_embed_body(request)
                response = await client.invoke_model(
                    modelId=self._context.model_name,
                    body=json.dumps(body),
                    contentType="application/json",
                )
                response_body = json.loads(response["body"].read())
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("embed", latency_ms)
            return EmbedResponse(
                embeddings=response_body.get("embedding", []),  # type: ignore[arg-type]
                model=self._context.model_name,
                usage=Usage(),
            )
        except Exception as exc:
            raise self._handle_provider_error(exc) from exc

    # ------------------------------------------------------------------
    # Rerank
    # ------------------------------------------------------------------

    async def _rerank(self, request: RerankRequest) -> RerankResponse:
        from app.core.exceptions import ProviderError

        raise ProviderError(
            provider_name=self._static.provider_name,
            message="Rerank is not supported by Bedrock (use Cohere via Bedrock marketplace if needed).",
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """Use Bedrock foundation-model listing as a health/permission probe."""
        from app.schemas.responses_schema import HealthStatus

        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(  # type: ignore[union-attr]
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
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
                detail=str(exc),
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
                "temperature": request.temperature or self._context.effective_temperature,
                "maxTokens": request.max_tokens or self._context.effective_max_tokens,
            },
        }

    def _build_converse_stream_payload(self, request: ChatRequest) -> dict[str, object]:
        """Build Bedrock Converse stream payload (currently same base fields)."""
        payload = self._build_converse_payload(request)
        payload["inferenceConfig"] = {
            **(payload.get("inferenceConfig", {})),  # type: ignore[arg-type]
        }
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
    def _parse_converse_response(response: dict[str, object]) -> ChatResponse:
        """Parse Bedrock Converse response into normalized ``ChatResponse``."""
        from app.schemas.responses_schema import ChatResponse, Usage

        output = response.get("output", {})
        message = output.get("message", {})  # type: ignore[union-attr]
        content_blocks = message.get("content", [])  # type: ignore[union-attr]
        text = "".join(
            block.get("text", "")
            for block in content_blocks  # type: ignore[union-attr]
        )
        usage_raw = response.get("usage", {})
        usage = (
            Usage(
                prompt_tokens=usage_raw.get("inputTokens", 0),  # type: ignore[union-attr]
                completion_tokens=usage_raw.get("outputTokens", 0),  # type: ignore[union-attr]
                total_tokens=usage_raw.get("totalTokens", 0),  # type: ignore[union-attr]
            )
            if usage_raw
            else None
        )
        return ChatResponse(
            content=text,
            role=message.get("role", "assistant"),  # type: ignore[union-attr]
            finish_reason=response.get("stopReason"),  # type: ignore[arg-type]
            usage=usage,
            model=response.get("modelId", ""),  # type: ignore[arg-type]
            raw_response=response,
        )

    @staticmethod
    def _parse_converse_stream_event(event: dict[str, object]) -> ChatStreamChunk:
        """Parse Bedrock stream event object into normalized stream chunk."""
        from app.schemas.responses_schema import ChatStreamChunk

        content = ""
        if "contentBlockDelta" in event:
            content = event["contentBlockDelta"].get("delta", {}).get("text", "") or ""  # type: ignore[index]

        finish_reason = None
        if "messageStop" in event:
            finish_reason = event["messageStop"].get("stopReason")  # type: ignore[index]

        return ChatStreamChunk(
            content=content,
            finish_reason=finish_reason,
            index=0,
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

