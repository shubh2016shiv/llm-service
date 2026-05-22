"""
LLM Inference Router - public inference endpoints for chat, embedding, and reranking.

Architecture:
-------------
    +------------------------------+
    ¦ Client (JWT + tenant headers)¦
    +------------------------------+
                    ?
    +------------------------------+
    ¦ this router (`/api/v1/llm/*`)|
    ¦ parse body + dependency chain¦
    +------------------------------+
                    ?
    +------------------------------+
    ¦ dependency layer             ¦
    ¦ auth + context resolution    ¦
    +------------------------------+
                    ?
    +------------------------------+
    ¦ InferenceService             ¦
    ¦ provider execution           ¦
    +------------------------------+
                    ?
    +------------------------------+
    ¦ provider adapter + response  ¦
    +------------------------------+

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

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    require_chat_execution_context,
    require_embed_execution_context,
    require_rerank_execution_context,
)
from app.api.exception_handlers import translate_inference_error
from app.core.exceptions import LLMServiceError
from app.inference_routing.models import ResolvedExecutionContext
from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses_schema import (
    ChatResponse,
    ChatStreamChunk,
    EmbedResponse,
    RerankResponse,
)
from app.services import InferenceService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["LLM Inference"])

# ---------------------------------------------------------------------------
# OpenAPI contract for the dual-mode /chat endpoint
#
# POST /chat can return either one JSON response or an SSE stream based on
# the `stream` flag in the request body:
#
#   stream=false (default) -> application/json -> ChatResponse
#   stream=true            -> text/event-stream -> stream of ChatStreamChunk
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
            "Each event is a `data:` line containing a JSON-encoded "
            "`ChatStreamChunk` (except `raw_chunk`), followed by a blank line. "
            "The stream always terminates with `data: [DONE]`. "
            "If provider execution fails mid-stream, one error event is sent "
            "before `[DONE]`: "
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
# In streaming mode, failures can happen after HTTP headers are sent. This
# helper converts runtime errors into one final SSE error event, then emits
# [DONE] so clients always receive a deterministic stream terminator.
# ---------------------------------------------------------------------------


async def _sse_stream(chunks: AsyncIterator[ChatStreamChunk]) -> AsyncIterator[str]:
    """Serialize streamed chat chunks into SSE `data:` events.

    Args:
        chunks: Async iterator of provider-generated chat chunks.

    Yields:
        str: SSE-formatted event payload lines.
    """
    try:
        async for chunk in chunks:
            payload = chunk.model_dump(exclude={"raw_chunk"})
            yield f"data: {json.dumps(payload)}\n\n"
    except LLMServiceError as exc:
        error_event: dict[str, Any] = {"error": {"code": exc.error_code, "message": str(exc)}}
        yield f"data: {json.dumps(error_event)}\n\n"
    finally:
        yield "data: [DONE]\n\n"


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat completion",
    description=(
        "Submit a conversation and receive a completion from the resolved deployment.\n\n"
        "JSON mode (`stream=false`, default): returns one `ChatResponse`.\n\n"
        "Stream mode (`stream=true`): returns `text/event-stream`; each `data:` line "
        "contains a `ChatStreamChunk` JSON object, and the stream ends with `[DONE]`."
    ),
    responses={
        200: {
            "description": (
                "Response format depends on request field `stream`.\n\n"
                "- `stream=false` -> JSON body (`ChatResponse`).\n"
                "- `stream=true` -> SSE event stream with `[DONE]` terminator."
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
    execution_context: Annotated[ResolvedExecutionContext, Depends(require_chat_execution_context)],
) -> ChatResponse | StreamingResponse:
    """Execute chat completion in JSON or streaming mode.

    Args:
        body: Chat prompt/messages and generation options.
        inference_service: Shared inference execution service.
        execution_context: Pre-resolved tenant/deployment/runtime context.

    Returns:
        ChatResponse | StreamingResponse: Standard JSON response when
            `body.stream` is false, otherwise an SSE stream.

    Raises:
        HTTPException: Raised indirectly after domain exceptions are translated.
    """
    try:
        if body.stream:
            chunks = inference_service.execute_stream_chat(context=execution_context, request=body)
            return StreamingResponse(
                _sse_stream(chunks),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return await inference_service.execute_chat(context=execution_context, request=body)

    except LLMServiceError as exc:
        logger.warning(
            "Chat request failed | tenant=%s quota_key=%s error_code=%s",
            execution_context.tenant_config.tenant_id,
            execution_context.quota_key,
            exc.error_code,
        )
        translate_inference_error(exc)


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
    execution_context: Annotated[ResolvedExecutionContext, Depends(require_embed_execution_context)],
) -> EmbedResponse:
    """Execute embedding generation for one authorized deployment.

    Args:
        body: Texts and embedding options.
        inference_service: Shared inference execution service.
        execution_context: Resolved tenant/deployment/runtime context.

    Returns:
        EmbedResponse: Embedding vectors and metadata.

    Raises:
        HTTPException: Raised indirectly after domain exceptions are translated.
    """
    try:
        return await inference_service.execute_embed(context=execution_context, request=body)
    except LLMServiceError as exc:
        logger.warning(
            "Embed request failed | tenant=%s quota_key=%s error_code=%s",
            execution_context.tenant_config.tenant_id,
            execution_context.quota_key,
            exc.error_code,
        )
        translate_inference_error(exc)


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
    execution_context: Annotated[
        ResolvedExecutionContext, Depends(require_rerank_execution_context)
    ],
) -> RerankResponse:
    """Execute reranking for a deployment configured with rerank capability.

    Args:
        body: Query and candidate documents for ranking.
        inference_service: Shared inference execution service.
        execution_context: Resolved tenant/deployment/runtime context.

    Returns:
        RerankResponse: Ranked candidates with scores.

    Raises:
        HTTPException: Raised indirectly after domain exceptions are translated.
    """
    try:
        return await inference_service.execute_rerank(context=execution_context, request=body)
    except LLMServiceError as exc:
        logger.warning(
            "Rerank request failed | tenant=%s quota_key=%s error_code=%s",
            execution_context.tenant_config.tenant_id,
            execution_context.quota_key,
            exc.error_code,
        )
        translate_inference_error(exc)

