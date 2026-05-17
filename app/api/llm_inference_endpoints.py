"""
LLM Inference Endpoints

Routes:
    POST /api/v1/llm/chat    — Chat completions (JSON body or SSE stream)
    POST /api/v1/llm/embed   — Text embeddings
    POST /api/v1/llm/rerank  — Document re-ranking

All routes require:
    - X-Tenant-ID header       (UUID identifying the requesting tenant)
    - X-Deployment-Key header  (string key selecting the target deployment)

The deployment key determines which provider, model, and credentials are used.
Callers never specify those directly — the resolver handles that.

NOTE: All routes require authentication via the require_developer guard.
Unauthenticated requests receive HTTP 401.

Dependency wiring:
    main.py must create one InferenceService during startup and store it on
    app.state.inference_service. The _get_inference_service() dependency below
    retrieves it from there, ensuring the ProviderRegistry singleton cache
    persists across requests.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.auth import require_developer
from app.core.exceptions import (
    AuthenticationError,
    ConcurrentRequestLimitError,
    DeploymentInactiveError,
    DeploymentNotFoundError,
    LLMServiceError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    QuotaExceededError,
    RateLimitError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.execution.inference_service import InferenceService
from app.routing.exceptions import OperationNotSupportedError, ProviderNotAllowedError
from app.schemas.auth_schemas import AuthTokenPayload
from app.schemas.requests import ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses import ChatResponse, EmbedResponse, RerankResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.schemas.responses import ChatStreamChunk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["LLM Inference"])


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
# Exception → HTTP status mapping
#
# The domain exception hierarchy (app.core.exceptions) is translated here to
# HTTP status codes. Walking the MRO means subclasses are matched before their
# base class — no explicit ordering of the dict entries is required.
# ---------------------------------------------------------------------------

_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantSuspendedError: status.HTTP_403_FORBIDDEN,
    DeploymentNotFoundError: status.HTTP_404_NOT_FOUND,
    DeploymentInactiveError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    QuotaExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ConcurrentRequestLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    # 502 — the service's stored credential is wrong, not the caller's fault.
    AuthenticationError: status.HTTP_502_BAD_GATEWAY,
    ProviderValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProviderNotAllowedError: status.HTTP_403_FORBIDDEN,
    OperationNotSupportedError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProviderUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ProviderTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}


def _raise_http(exc: LLMServiceError) -> NoReturn:
    """Translate a domain exception to an HTTPException and raise it.

    Walks the exception's MRO so the most specific mapping wins.
    Falls back to HTTP 500 for any LLMServiceError not in the map.
    """
    for exc_type in type(exc).__mro__:
        if exc_type in _EXCEPTION_STATUS:
            raise HTTPException(
                status_code=_EXCEPTION_STATUS[exc_type],
                detail=str(exc),
            )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred.",
    )


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
        "Submit a conversation and receive a completion from the resolved deployment. "
        "Set stream=true to receive a server-sent event stream rather than a JSON body."
    ),
)
async def chat_completion(
    body: ChatRequest,
    x_tenant_id: Annotated[str, Header()],
    x_deployment_key: Annotated[str, Header()],
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ChatResponse | StreamingResponse:
    try:
        if body.stream:
            chunks = inference_service.execute_stream_chat(
                tenant_id=x_tenant_id,
                deployment_key=x_deployment_key,
                request=body,
            )
            return StreamingResponse(
                _sse_stream(chunks),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return await inference_service.execute_chat(
            tenant_id=x_tenant_id,
            deployment_key=x_deployment_key,
            request=body,
        )

    except LLMServiceError as exc:
        logger.warning(
            "Chat request failed | tenant=%s deployment=%s error_code=%s",
            x_tenant_id,
            x_deployment_key,
            exc.error_code,
        )
        _raise_http(exc)


@router.post(
    "/embed",
    response_model=EmbedResponse,
    status_code=status.HTTP_200_OK,
    summary="Text embeddings",
    description="Embed one or more texts using the deployment's embedding model.",
)
async def embed(
    body: EmbedRequest,
    x_tenant_id: Annotated[str, Header()],
    x_deployment_key: Annotated[str, Header()],
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> EmbedResponse:
    try:
        return await inference_service.execute_embed(
            tenant_id=x_tenant_id,
            deployment_key=x_deployment_key,
            request=body,
        )
    except LLMServiceError as exc:
        logger.warning(
            "Embed request failed | tenant=%s deployment=%s error_code=%s",
            x_tenant_id,
            x_deployment_key,
            exc.error_code,
        )
        _raise_http(exc)


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
    x_tenant_id: Annotated[str, Header()],
    x_deployment_key: Annotated[str, Header()],
    inference_service: Annotated[InferenceService, Depends(_get_inference_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> RerankResponse:
    try:
        return await inference_service.execute_rerank(
            tenant_id=x_tenant_id,
            deployment_key=x_deployment_key,
            request=body,
        )
    except LLMServiceError as exc:
        logger.warning(
            "Rerank request failed | tenant=%s deployment=%s error_code=%s",
            x_tenant_id,
            x_deployment_key,
            exc.error_code,
        )
        _raise_http(exc)
