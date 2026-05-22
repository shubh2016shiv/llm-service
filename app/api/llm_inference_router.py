"""
LLM Inference Endpoints
========================

This module defines the public-facing API routes that end users call to
interact with AI models — chat, embeddings, and re-ranking.

Endpoints:
    POST /api/v1/llm/chat    — Chat completions (can return JSON or stream)
    POST /api/v1/llm/embed   — Convert text into vector embeddings
    POST /api/v1/llm/rerank  — Re-rank a list of documents by relevance

All three endpoints require two headers that tell the system which tenant
and which model deployment to use:
    - X-Tenant-ID        (UUID of the tenant making the request)
    - X-Deployment-Key    (a short string key that identifies one deployment)

The deployment key is how the system resolves the caller's intent to a
specific AI provider, model, and credentials. The caller never specifies
the provider or model directly — the deployment configuration handles that
mapping.

Authentication and Authorization:
    Every route requires the caller to be authenticated (HTTP 401 if
    not) and authorized for the specific tenant and deployment (HTTP 403
    if the tenant/deployment access check fails). This is enforced by the
    ``require_inference_access`` FastAPI dependency.

Enterprise Pattern: API Gateway Router Pattern
    This router acts as the single entry point for all inference requests.
    It enforces authentication and authorization before any business logic
    runs, then delegates to the InferenceService to do the actual work.
    Separating inference from management endpoints keeps each group's
    security rules and error handling independent.

Author: Shubham Singh
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import require_inference_access
from app.api.exception_handlers import translate_inference_error
from app.core.exceptions import LLMServiceError
from app.schemas.auth_schema import InferenceAccessContext
from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses_schema import (
    ChatResponse,
    ChatStreamChunk,
    EmbedResponse,
    RerankResponse,
)
from app.services.inference_service import InferenceService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["LLM Inference"])

# ---------------------------------------------------------------------------
# OpenAPI contract for the dual-mode /chat endpoint
#
# POST /chat has two distinct response shapes depending on the `stream` flag
# in the request body:
#
#   stream=false (default) → application/json       → ChatResponse
#   stream=true            → text/event-stream (SSE) → stream of ChatStreamChunk
#
# FastAPI auto-generates the application/json entry from response_model=ChatResponse.
# The SSE entry must be declared manually via the `responses` parameter because
# FastAPI has no built-in concept of server-sent event streams.
#
# The SSE wire format (RFC 8895):
#   data: <JSON-encoded ChatStreamChunk, raw_chunk excluded>\n\n
#   ...
#   data: [DONE]\n\n                       ← terminal sentinel, always present
#   data: {"error": {"code": "...", "message": "..."}}\n\n  ← only on error
# ---------------------------------------------------------------------------

_CHAT_STREAM_CHUNK_SCHEMA: dict[str, object] = ChatStreamChunk.model_json_schema()

_CHAT_SSE_RESPONSE_CONTENT: dict[str, object] = {
    "schema": {
        "type": "string",
        "description": (
            "Server-sent event stream (RFC 8895). "
            "Each event is a `data:` line containing a JSON-encoded `ChatStreamChunk` "
            "(all fields except `raw_chunk`), followed by a blank line. "
            "The stream always terminates with `data: [DONE]`. "
            "On a mid-stream provider error a single error event is emitted before [DONE]: "
            '`data: {"error": {"code": "<ERROR_CODE>", "message": "<detail>"}}`.'
        ),
    },
    "example": (
        'data: {"content": "The", "finish_reason": null, "index": 0}\n\n'
        'data: {"content": " answer", "finish_reason": null, "index": 0}\n\n'
        'data: {"content": " is 42.", "finish_reason": null, "index": 0}\n\n'
        'data: {"content": "", "finish_reason": "stop", "index": 0}\n\n'
        "data: [DONE]\n\n"
    ),
}


# ---------------------------------------------------------------------------
# Dependency: InferenceService
#
# InferenceService owns the ProviderRegistry singleton cache. It must be
# created once at process startup (in main.py's lifespan handler) and stored
# on app.state so that the cache survives across requests. Creating it per-
# request would destroy and recreate the provider cache on every call.
# ---------------------------------------------------------------------------


def _get_inference_service(request: Request) -> InferenceService:
    """Retrieve the process-scoped InferenceService from app.state.

    Raises:
        RuntimeError: If main.py has not populated app.state.inference_service.
    """
    service: InferenceService | None = getattr(request.app.state, "inference_service", None)
    if service is None:
        raise RuntimeError(
            "app.state.inference_service is not initialised. "
            "Ensure the lifespan handler in main.py creates and stores "
            "an InferenceService instance before the application accepts traffic."
        )
    return service


# ---------------------------------------------------------------------------
# Streaming helper
#
# execute_stream_chat is an async generator. Resolution, quota check, and
# provider lookup all happen during the first iteration step — after the HTTP
# 200 header has already been sent. Any LLMServiceError raised there is caught
# and serialised as an error SSE event so the client can handle it gracefully.
# ---------------------------------------------------------------------------


async def _sse_stream(chunks: AsyncIterator[ChatStreamChunk]) -> AsyncIterator[str]:
    """Yield server-sent event strings from a ChatStreamChunk iterator."""
    try:
        async for chunk in chunks:
            payload = chunk.model_dump(exclude={"raw_chunk"})
            yield f"data: {json.dumps(payload)}\n\n"
    except LLMServiceError as exc:
        error_event: dict[str, Any] = {"error": {"code": exc.error_code, "message": str(exc)}}
        yield f"data: {json.dumps(error_event)}\n\n"
    finally:
        yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat completion",
    description=(
        "Submit a conversation and receive a completion from the resolved deployment.\n\n"
        "**JSON mode** (`stream=false`, default): "
        "Returns a single `ChatResponse` object as `application/json`.\n\n"
        "**Stream mode** (`stream=true`): "
        "Returns a `text/event-stream` (SSE) response. "
        "Each `data:` line carries a JSON-encoded `ChatStreamChunk` (all fields except `raw_chunk`). "
        "The stream always ends with `data: [DONE]`. "
        "On a provider error a single error event is emitted before [DONE]."
    ),
    responses={
        200: {
            "description": (
                "Chat completion — response shape depends on the `stream` field in the request body.\n\n"
                "- `stream=false` → `application/json` body containing a `ChatResponse` object.\n"
                "- `stream=true`  → `text/event-stream` body; each `data:` line is a "
                "JSON-encoded `ChatStreamChunk`; stream ends with `data: [DONE]`."
            ),
            "content": {
                "text/event-stream": _CHAT_SSE_RESPONSE_CONTENT,
            },
        },
    },
)
async def chat_completion(
    body: ChatRequest,
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
) -> ChatResponse | StreamingResponse:
    try:
        if body.stream:
            chunks = inference_service.execute_stream_chat(
                tenant_id=inference_context.tenant_id,
                deployment_key=inference_context.deployment_key,
                request=body,
            )
            return StreamingResponse(
                _sse_stream(chunks),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return await inference_service.execute_chat(
            tenant_id=inference_context.tenant_id,
            deployment_key=inference_context.deployment_key,
            request=body,
        )

    except LLMServiceError as exc:
        logger.warning(
            "Chat request failed | tenant=%s deployment=%s error_code=%s",
            inference_context.tenant_id,
            inference_context.deployment_key,
            exc.error_code,
        )
        translate_inference_error(exc)


@router.post(
    "/embed",
    response_model=EmbedResponse,
    status_code=status.HTTP_200_OK,
    summary="Text embeddings",
    description="Embed one or more texts using the deployment's embedding model.",
)
async def embed(
    body: EmbedRequest,
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
) -> EmbedResponse:
    try:
        return await inference_service.execute_embed(
            tenant_id=inference_context.tenant_id,
            deployment_key=inference_context.deployment_key,
            request=body,
        )
    except LLMServiceError as exc:
        logger.warning(
            "Embed request failed | tenant=%s deployment=%s error_code=%s",
            inference_context.tenant_id,
            inference_context.deployment_key,
            exc.error_code,
        )
        translate_inference_error(exc)


@router.post(
    "/rerank",
    response_model=RerankResponse,
    status_code=status.HTTP_200_OK,
    summary="Document re-ranking",
    description=(
        "Re-rank a list of documents against a query. "
        "Only deployments backed by a model that supports re-ranking will succeed; "
        "others return HTTP 422."
    ),
)
async def rerank(
    body: RerankRequest,
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
) -> RerankResponse:
    try:
        return await inference_service.execute_rerank(
            tenant_id=inference_context.tenant_id,
            deployment_key=inference_context.deployment_key,
            request=body,
        )
    except LLMServiceError as exc:
        logger.warning(
            "Rerank request failed | tenant=%s deployment=%s error_code=%s",
            inference_context.tenant_id,
            inference_context.deployment_key,
            exc.error_code,
        )
        translate_inference_error(exc)
