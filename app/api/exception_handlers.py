"""
Exception translation utilities for API routes.

Architecture:
-------------
    +--------------------------+
    ¦ Service/domain exception ¦
    +--------------------------+
                 ?
    +--------------------------+
    ¦ translate_* functions    ¦
    ¦ (this module)            ¦
    +--------------------------+
                 ?
    +--------------------------+
    ¦ HTTPException / JSON body¦
    +--------------------------+

Purpose:
    Route handlers should not decide HTTP status mapping for every domain
    failure. This module centralizes that mapping so behavior is consistent and
    easy to extend.

Jargon explained:
    - Domain exception: business-level error class (tenant suspended, quota hit,
      invalid state transition) independent from HTTP protocol details.
    - MRO (method resolution order): Python class inheritance order used here
      to map subclasses to the most specific known status code.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AuthenticationError,
    ConcurrentRequestLimitError,
    DeploymentInactiveError,
    DeploymentNotFoundError,
    InvalidStateTransitionError,
    LLMServiceError,
    ManagementError,
    ManagementValidationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    QuotaExceededError,
    RateLimitError,
    ResourceConflictError,
    ResourceNotFoundError,
    TenantAccessDeniedError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.core.request_context import get_request_id
from app.inference_routing.exceptions import OperationNotSupportedError, ProviderNotAllowedError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inference exception -> HTTP status mapping
# ---------------------------------------------------------------------------

_INFERENCE_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantSuspendedError: status.HTTP_403_FORBIDDEN,
    DeploymentNotFoundError: status.HTTP_404_NOT_FOUND,
    DeploymentInactiveError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    QuotaExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ConcurrentRequestLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    # Provider credential or auth failure on our side, not caller JWT failure.
    AuthenticationError: status.HTTP_502_BAD_GATEWAY,
    ProviderValidationError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ProviderNotAllowedError: status.HTTP_403_FORBIDDEN,
    OperationNotSupportedError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ProviderUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ProviderTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}


# ---------------------------------------------------------------------------
# Management exception -> HTTP status mapping
# ---------------------------------------------------------------------------

_MANAGEMENT_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    ResourceNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantAccessDeniedError: status.HTTP_403_FORBIDDEN,
    InvalidStateTransitionError: status.HTTP_409_CONFLICT,
    ResourceConflictError: status.HTTP_409_CONFLICT,
    ManagementValidationError: status.HTTP_400_BAD_REQUEST,
    ManagementError: status.HTTP_400_BAD_REQUEST,
}


# ---------------------------------------------------------------------------
# Fallback exception -> HTTP status mapping
# ---------------------------------------------------------------------------

_FALLBACK_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    **_MANAGEMENT_EXCEPTION_STATUS,
    TenantSuspendedError: status.HTTP_403_FORBIDDEN,
    DeploymentNotFoundError: status.HTTP_404_NOT_FOUND,
    DeploymentInactiveError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    QuotaExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ConcurrentRequestLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    AuthenticationError: status.HTTP_502_BAD_GATEWAY,
    ProviderValidationError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ProviderUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ProviderTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}


def _resolve_status(
    exc: LLMServiceError,
    mapping: dict[type[LLMServiceError], int],
    fallback_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> int:
    """Resolve HTTP status by walking exception inheritance order.

    Args:
        exc: Domain exception to map.
        mapping: Status mapping keyed by exception classes.
        fallback_status: Status returned when no mapping exists.

    Returns:
        int: HTTP status code.
    """
    for exc_type in type(exc).__mro__:
        if exc_type in mapping:
            return mapping[exc_type]  # type: ignore[index]
    return fallback_status


def _retry_after_headers(exc: LLMServiceError) -> dict[str, str]:
    """Build optional `Retry-After` header for rate limit style failures.

    Args:
        exc: Domain exception that may carry `retry_after_seconds`.

    Returns:
        dict[str, str]: Header mapping with `Retry-After` when available.
    """
    retry_seconds: int | None = getattr(exc, "retry_after_seconds", None)
    if retry_seconds is not None:
        return {"Retry-After": str(retry_seconds)}
    return {}


def translate_inference_error(exc: LLMServiceError) -> NoReturn:
    """Translate an inference domain exception into HTTPException.

    Args:
        exc: Domain-level inference failure.

    Raises:
        HTTPException: Always raised with mapped status and detail.
    """
    resolved_status = _resolve_status(exc, _INFERENCE_EXCEPTION_STATUS)
    raise HTTPException(
        status_code=resolved_status,
        detail=str(exc),
        headers=_retry_after_headers(exc) or None,
    )


def translate_management_error(exc: LLMServiceError) -> NoReturn:
    """Translate a management domain exception into HTTPException.

    Also logs one structured warning so management failures always appear in
    logs with request correlation id.

    Args:
        exc: Domain-level management failure.

    Raises:
        HTTPException: Always raised with mapped status and detail.
    """
    resolved_status = _resolve_status(exc, _MANAGEMENT_EXCEPTION_STATUS)

    logger.warning(
        "Management exception translated | request_id=%s exc_type=%s status=%d detail=%s",
        get_request_id(),
        type(exc).__name__,
        resolved_status,
        str(exc),
    )

    raise HTTPException(status_code=resolved_status, detail=str(exc)) from exc


async def _on_unhandled_llm_service_error(request: Request, exc: LLMServiceError) -> JSONResponse:
    """Safety net for uncaught domain exceptions at global app boundary.

    This should be rare. When it triggers, route-level exception translation is
    missing in at least one route path.

    Args:
        request: Request that produced the exception.
        exc: Escaped domain exception.

    Returns:
        JSONResponse: Structured error response with resolved status.
    """
    resolved_status = _resolve_status(exc, _FALLBACK_EXCEPTION_STATUS)

    if resolved_status >= 500:
        logger.error(
            "Unhandled domain exception reached global handler | "
            "request_id=%s method=%s path=%s exc_type=%s",
            get_request_id(),
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=True,
        )
    else:
        logger.warning(
            "Domain exception escaped route handler | "
            "request_id=%s method=%s path=%s exc_type=%s status=%d",
            get_request_id(),
            request.method,
            request.url.path,
            type(exc).__name__,
            resolved_status,
        )

    return JSONResponse(
        status_code=resolved_status,
        content={"detail": str(exc)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app.

    Args:
        app: FastAPI application instance.
    """
    app.add_exception_handler(LLMServiceError, _on_unhandled_llm_service_error)  # type: ignore[arg-type]

