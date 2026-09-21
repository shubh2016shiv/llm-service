"""
LLM Inference Router - public inference endpoints for chat, embedding, and reranking.

Architecture:
-------------
    ┌───────────────────────────────┐
    │ Client (JWT + tenant headers) │
    └───────────────┬───────────────┘
                     ▼
    ┌───────────────────────────────┐
    │ this router (`/api/v1/llm/*`) │
    │ parse body + dependency chain │
    └───────────────┬───────────────┘
                    ▼
    ┌───────────────────────────────┐
    │ dependency layer              │
    │ auth + context resolution     │
    └───────────────┬───────────────┘
                    ▼
    ┌───────────────────────────────┐
    │ InferenceService              │
    │ provider execution            │
    └───────────────┬───────────────┘
                    ▼
    ┌───────────────────────────────┐
    │ provider adapter + response   │
    └───────────────────────────────┘

Flow rationale:
    Clients do not choose provider/model directly. They send `X-Tenant-ID` and
    `X-Deployment-Key`; the deployment configuration decides provider, model,
    credential scope, and policy checks. This keeps client APIs stable even when
    provider choices change internally.

Jargon explained:
    - SSE (Server-Sent Events): an HTTP response that streams incremental
      events over one connection instead of returning one final JSON body.
    - Execution context: a resolved runtime bundle containing tenant policy,
      selected deployment, model capability checks, and quota key.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.api.exception_handlers import translate_inference_error
from app.api.inference_dependencies import (
    require_chat_route,
    require_embed_route,
    require_inference_access,
    require_rerank_route,
)
from app.api.shared_dependencies import require_app_state
from app.core.exceptions import LLMServiceError
from app.inference_routing.models import ResolvedRoute
from app.schemas.auth_schema import InferenceAccessContext
from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses_schema import (
    ChatResponse,
    ChatStreamChunk,
    EmbedResponse,
    RerankResponse,
)
from app.services import InferenceService
from app.streaming.chat_chunk_adapter import adapt_chat_chunks, map_llm_stream_error
from app.streaming.sse_delivery import SSEStreamDelivery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["LLM Inference"])

# ---------------------------------------------------------------------------
# OpenAPI contract for the dual-mode /chat endpoint
#
# POST /chat can return either one JSON response or an SSE stream based on
# the `stream` flag in the request body:
#
#   stream=false (default) -> application/json -> ChatResponse
#   stream=true            -> text/event-stream -> thread-scoped event envelopes
#
# FastAPI generates JSON schema automatically from response_model.
# SSE schema is added manually because it is a streaming wire contract.
# ---------------------------------------------------------------------------

_CHAT_STREAM_CHUNK_SCHEMA: dict[str, object] = ChatStreamChunk.model_json_schema()

_CHAT_SSE_RESPONSE_CONTENT: dict[str, object] = {
    "schema": {
        "type": "string",
        "description": (
            "Server-sent event stream. "
            "Named `text_delta` and `stream_metadata` events contain a thread ID, "
            "monotonic sequence, optional request ID, and event data. Comment "
            "heartbeats keep quiet connections alive. One named `complete` event "
            "terminates the stream; post-header failures use a named `error` event."
        ),
    },
    "example": (
        "event: text_delta\n"
        'data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000",'
        '"sequence":1,"request_id":"req-1","data":{"content":"The","index":0}}\n\n'
        "event: complete\n"
        'data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000",'
        '"sequence":2,"request_id":"req-1","data":{"status":"completed"}}\n\n'
    ),
}


# ---------------------------------------------------------------------------
# Dependency: InferenceService
#
# InferenceService owns provider registry caches and adapter clients. It should
# be created once during app startup and reused via app.state.
# ---------------------------------------------------------------------------


def _get_inference_service(request: Request) -> InferenceService:
    """Return the process-scoped InferenceService from `request.app.state`.

    Args:
        request: FastAPI request object carrying application state.

    Returns:
        InferenceService: Startup-initialized inference orchestrator.

    Raises:
        RuntimeError: If startup lifecycle did not initialize the service.
    """
    return require_app_state(
        request,
        "inference_service",
        InferenceService,
        hint=(
            "Ensure the lifespan handler in main.py creates and stores "
            "an InferenceService instance before the application accepts traffic."
        ),
    )


# Stage 1:1 - Check the caller's route access, select the provider, and return or stream chat text.
@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat completion",
    description=(
        "Submit a conversation and receive a completion from the resolved deployment.\n\n"
        "JSON mode (`stream=false`, default): returns one `ChatResponse`.\n\n"
        "Stream mode (`stream=true`): returns thread-scoped `text/event-stream` "
        "messages and ends with one named `complete` event."
    ),
    responses={
        200: {
            "description": (
                "Response format depends on request field `stream`.\n\n"
                "- `stream=false` -> JSON body (`ChatResponse`).\n"
                "- `stream=true` -> thread-scoped SSE events ending in `complete`."
            ),
            "content": {
                "text/event-stream": _CHAT_SSE_RESPONSE_CONTENT,
            },
        },
    },
)
async def chat_completion(
    body: ChatRequest,
    http_request: Request,
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
    resolved_route: Annotated[ResolvedRoute, Depends(require_chat_route)],
) -> ChatResponse | StreamingResponse:
    """Execute chat completion in JSON or streaming mode.

    Args:
        body: Chat prompt/messages and generation options.
        http_request: Request state carrying correlation and application settings.
        inference_service: Shared inference execution service.
        resolved_route: Pre-resolved provider route.

    Returns:
        ChatResponse | StreamingResponse: Standard JSON response when
            `body.stream` is false, otherwise an SSE stream.

    Raises:
        HTTPException: Raised indirectly after domain exceptions are translated.
    """
    try:
        if body.stream:
            request_id = getattr(http_request.state, "request_id", None)
            chunks = await inference_service.prepare_stream_chat(
                context=resolved_route,
                request=body,
                user_id=inference_context.user_id,
                request_id=request_id,
            )
            heartbeat_interval = require_app_state(
                http_request,
                "stream_heartbeat_interval_seconds",
                float,
                hint="Initialize streaming settings during application startup.",
            )
            delivery = SSEStreamDelivery(
                heartbeat_interval_seconds=heartbeat_interval,
                error_mapper=map_llm_stream_error,
            )
            return StreamingResponse(
                delivery.stream(
                    adapt_chat_chunks(chunks),
                    thread_id=body.thread_id,
                    request_id=request_id,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                },
            )

        return await inference_service.execute_chat(
            context=resolved_route,
            request=body,
            user_id=inference_context.user_id,
        )

    except LLMServiceError as exc:
        logger.warning(
            "Chat request failed | tenant=%s quota_key=%s error_code=%s",
            resolved_route.tenant_id,
            resolved_route.quota_key,
            exc.error_code,
        )
        translate_inference_error(exc)


# Stage 1:2 - Check route access and model support, then return vectors for the supplied text.
@router.post(
    "/embed",
    response_model=EmbedResponse,
    status_code=status.HTTP_200_OK,
    summary="Text embeddings",
    description="Convert one or more input texts into embedding vectors.",
)
async def embed(
    body: EmbedRequest,
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
    resolved_route: Annotated[ResolvedRoute, Depends(require_embed_route)],
) -> EmbedResponse:
    """Execute embedding generation for one authorized deployment.

    Args:
        body: Texts and embedding options.
        inference_service: Shared inference execution service.
        resolved_route: Pre-resolved provider route.

    Returns:
        EmbedResponse: Embedding vectors and metadata.

    Raises:
        HTTPException: Raised indirectly after domain exceptions are translated.
    """
    try:
        return await inference_service.execute_embed(
            context=resolved_route,
            request=body,
            user_id=inference_context.user_id,
        )
    except LLMServiceError as exc:
        logger.warning(
            "Embed request failed | tenant=%s quota_key=%s error_code=%s",
            resolved_route.tenant_id,
            resolved_route.quota_key,
            exc.error_code,
        )
        translate_inference_error(exc)


# Stage 1:3 - Check route access and model support, then return documents ordered by relevance.
@router.post(
    "/rerank",
    response_model=RerankResponse,
    status_code=status.HTTP_200_OK,
    summary="Document re-ranking",
    description=(
        "Re-rank a document list against a query. "
        "If the resolved model lacks rerank capability, request fails with HTTP 422."
    ),
)
async def rerank(
    body: RerankRequest,
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    inference_context: Annotated[InferenceAccessContext, Depends(require_inference_access)],
    resolved_route: Annotated[ResolvedRoute, Depends(require_rerank_route)],
) -> RerankResponse:
    """Execute reranking for a deployment configured with rerank capability.

    Args:
        body: Query and candidate documents for ranking.
        inference_service: Shared inference execution service.
        resolved_route: Pre-resolved provider route.

    Returns:
        RerankResponse: Ranked candidates with scores.

    Raises:
        HTTPException: Raised indirectly after domain exceptions are translated.
    """
    try:
        return await inference_service.execute_rerank(
            context=resolved_route,
            request=body,
            user_id=inference_context.user_id,
        )
    except LLMServiceError as exc:
        logger.warning(
            "Rerank request failed | tenant=%s quota_key=%s error_code=%s",
            resolved_route.tenant_id,
            resolved_route.quota_key,
            exc.error_code,
        )
        translate_inference_error(exc)
