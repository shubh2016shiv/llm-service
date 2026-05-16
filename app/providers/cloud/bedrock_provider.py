"""
app/providers/cloud/bedrock_provider.py — AWS Bedrock runtime provider.

Architecture
------------
    BaseProvider (ABC)
        └── BedrockProvider   ← chat, embed via boto3 bedrock-runtime

Auth: AWS SigV4 via boto3 session (no API key in headers).
Transport: aioboto3 (async AWS SDK) — NOT httpx.

Key design differences from direct/ providers:
- Uses aioboto3 instead of httpx.AsyncClient.
- Authentication is handled by boto3's credential chain (env vars, IAM, etc.).
- Converts domain requests ↔ Bedrock-specific JSON schemas.
- AWS credentials flow through the AWS SDK, not our SecretStore.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from app.providers.base_provider import BaseProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import aiobreaker

    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import DeploymentConfig
    from app.schemas.requests import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses import (
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
    """

    def __init__(
        self,
        static_config: ProviderStaticConfig,
        deployment_config: DeploymentConfig,
        http_client: object,  # aioboto3.Session in practice; typed loosely for ABC compatibility
        circuit_breaker: aiobreaker.CircuitBreaker,
    ) -> None:
        super().__init__(static_config, deployment_config, http_client, circuit_breaker)
        self._bedrock_session = http_client  # stored as the aioboto3 session

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _generate(self, request: ChatRequest) -> ChatResponse:
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
        from app.schemas.responses import EmbedResponse, Usage

        t0 = time.monotonic()
        try:
            async with self._bedrock_session.client(  # type: ignore[union-attr]
                "bedrock-runtime",
                region_name=self._resolve_aws_region(),
            ) as client:
                # Bedrock uses InvokeModel for embeddings (pre-Converse API)
                body = self._build_embed_body(request)
                response = await client.invoke_model(
                    modelId=self._deployment.model_name,
                    body=json.dumps(body),
                    contentType="application/json",
                )
                response_body = json.loads(response["body"].read())
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("embed", latency_ms)
            return EmbedResponse(
                embeddings=response_body.get("embedding", []),  # type: ignore[arg-type]
                model=self._deployment.model_name,
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
        from app.schemas.responses import HealthStatus

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
        return {
            "modelId": self._deployment.model_name,
            "messages": self._convert_messages_to_bedrock(request),
            "inferenceConfig": {
                "temperature": request.temperature or self._deployment.default_temperature,
                "maxTokens": request.max_tokens or self._deployment.default_max_tokens or 512,
            },
        }

    def _build_converse_stream_payload(self, request: ChatRequest) -> dict[str, object]:
        payload = self._build_converse_payload(request)
        payload["inferenceConfig"] = {
            **(payload.get("inferenceConfig", {})),  # type: ignore[arg-type]
        }
        return payload

    def _build_embed_body(self, request: EmbedRequest) -> dict[str, object]:
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
        from app.schemas.responses import ChatResponse, Usage

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
        from app.schemas.responses import ChatStreamChunk

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
        """Resolve AWS region from deployment settings or provider defaults."""
        value = self._deployment.extra_config.get("aws_region")
        if isinstance(value, str) and value:
            return value
        return "us-east-1"
