"""Translate application failures into one safe, observable HTTP contract.

Why this module exists
----------------------
Domain and service code should describe *what failed* without knowing HTTP.
This boundary decides the status code and response envelope in one place.

Every JSON error has three stable fields:
    ``detail``      Human-readable explanation.
    ``error_code``  Machine-readable category for clients and metrics.
    ``request_id``  Correlation value shared with logs and response headers.

Validation deserves special treatment. Pydantic error dictionaries include the
rejected ``input`` value; returning that unchanged could echo an API key or
password from a credential-management request. The validation handler exposes
only location, message, and type.
"""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AuthenticationError,
    AuthorizationGrantCacheUnavailableError,
    ConcurrentRequestLimitError,
    DeploymentInactiveError,
    DeploymentNotFoundError,
    GuestSessionDisabledError,
    InvalidCredentialsError,
    InvalidStateTransitionError,
    LLMServiceError,
    ManagementError,
    ManagementValidationError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    QuotaExceededError,
    RateLimitError,
    ResourceConflictError,
    ResourceNotFoundError,
    SecretBackendUnavailableError,
    SignInError,
    TenantAccessDeniedError,
    TenantNotFoundError,
    TenantSuspendedError,
    TooManySignInAttemptsError,
)
from app.inference_routing.exceptions import (
    AuthorizedEntitlementUnavailableError,
    OperationNotSupportedError,
    ProviderNotAllowedError,
)

logger = logging.getLogger(__name__)

_INFERENCE_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantSuspendedError: status.HTTP_403_FORBIDDEN,
    TenantAccessDeniedError: status.HTTP_403_FORBIDDEN,
    DeploymentNotFoundError: status.HTTP_404_NOT_FOUND,
    DeploymentInactiveError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    QuotaExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ConcurrentRequestLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    AuthenticationError: status.HTTP_502_BAD_GATEWAY,
    ProviderValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProviderNotAllowedError: status.HTTP_403_FORBIDDEN,
    OperationNotSupportedError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AuthorizedEntitlementUnavailableError: status.HTTP_403_FORBIDDEN,
    ProviderUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ProviderTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
    SecretBackendUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # A generic adapter failure describes a bad upstream response, not an
    # internal crash in this API process.
    ProviderError: status.HTTP_502_BAD_GATEWAY,
}

# Sign-in outcomes. 401 is the honest answer for a rejected credential; the
# limiter's 429 carries Retry-After via the shared _retry_after_headers helper.
# Guest refusal is 403 rather than 404: the route exists, the deployment simply
# does not offer that door.
_SIGN_IN_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    GuestSessionDisabledError: status.HTTP_403_FORBIDDEN,
    TooManySignInAttemptsError: status.HTTP_429_TOO_MANY_REQUESTS,
    SignInError: status.HTTP_401_UNAUTHORIZED,
}

_MANAGEMENT_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    AuthorizationGrantCacheUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    SecretBackendUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ResourceNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantAccessDeniedError: status.HTTP_403_FORBIDDEN,
    InvalidStateTransitionError: status.HTTP_409_CONFLICT,
    ResourceConflictError: status.HTTP_409_CONFLICT,
    ManagementValidationError: status.HTTP_400_BAD_REQUEST,
    ManagementError: status.HTTP_400_BAD_REQUEST,
}

# Typed exceptions can escape dependencies before a route function starts.
# The global safety-net map therefore covers both API families.
_FALLBACK_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    **_MANAGEMENT_EXCEPTION_STATUS,
    **_INFERENCE_EXCEPTION_STATUS,
    **_SIGN_IN_EXCEPTION_STATUS,
}


class DomainHTTPException(HTTPException):
    """HTTP representation of a domain failure with a stable machine code."""

    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        error_code: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code


def _resolve_status(
    exc: LLMServiceError,
    mapping: dict[type[LLMServiceError], int],
    fallback_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> int:
    """Use the exception inheritance chain to find the most specific status."""
    for exc_type in type(exc).__mro__:
        if exc_type in mapping:
            return mapping[exc_type]
    return fallback_status


def _retry_after_headers(exc: LLMServiceError) -> dict[str, str]:
    """Expose a positive integer retry hint when a domain error carries one."""
    retry_seconds = getattr(exc, "retry_after_seconds", None)
    if isinstance(retry_seconds, int) and retry_seconds > 0:
        return {"Retry-After": str(retry_seconds)}
    return {}


def translate_inference_error(exc: LLMServiceError) -> NoReturn:
    """Raise the HTTP form of a known inference-domain failure."""
    raise DomainHTTPException(
        status_code=_resolve_status(exc, _INFERENCE_EXCEPTION_STATUS),
        detail=str(exc),
        error_code=exc.error_code,
        headers=_retry_after_headers(exc) or None,
    ) from exc


def translate_management_error(exc: LLMServiceError) -> NoReturn:
    """Raise the HTTP form of a known management-domain failure."""
    raise DomainHTTPException(
        status_code=_resolve_status(exc, _MANAGEMENT_EXCEPTION_STATUS),
        detail=str(exc),
        error_code=exc.error_code,
    ) from exc


def translate_sign_in_error(exc: LLMServiceError) -> NoReturn:
    """Raise the HTTP form of a failed sign-in attempt.

    Separate from ``translate_management_error`` because the management map
    would resolve an unrecognized sign-in failure to 400. A credential problem
    is 401, and answering 400 would tell a client to fix its request body when
    the request was well-formed and simply not authorized.
    """
    raise DomainHTTPException(
        status_code=_resolve_status(
            exc,
            _SIGN_IN_EXCEPTION_STATUS,
            fallback_status=status.HTTP_401_UNAUTHORIZED,
        ),
        detail=str(exc),
        error_code=exc.error_code,
        headers=_retry_after_headers(exc) or None,
    ) from exc


def _error_content(*, detail: object, error_code: str, request_id: str | None) -> dict[str, object]:
    """Build the backward-compatible JSON envelope shared by all handlers."""
    return {"detail": detail, "error_code": error_code, "request_id": request_id}


async def _on_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Normalize framework, authentication, and translated domain failures."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_content(
            detail=exc.detail,
            error_code=getattr(exc, "error_code", "HTTP_ERROR"),
            request_id=getattr(request.state, "request_id", None),
        ),
        headers=exc.headers,
    )


async def _on_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return field diagnostics without echoing rejected request values."""
    safe_errors = [
        {
            "location": list(error.get("loc", ())),
            "message": str(error.get("msg", "Invalid value.")),
            "type": str(error.get("type", "value_error")),
        }
        for error in exc.errors()
    ]
    content = _error_content(
        detail="Request validation failed.",
        error_code="REQUEST_VALIDATION_ERROR",
        request_id=getattr(request.state, "request_id", None),
    )
    content["errors"] = safe_errors
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=content)


async def _on_unhandled_llm_service_error(
    request: Request,
    exc: LLMServiceError,
) -> JSONResponse:
    """Translate a typed error that escaped a route or dependency."""
    resolved_status = _resolve_status(exc, _FALLBACK_EXCEPTION_STATUS)
    request_id = getattr(request.state, "request_id", None)
    log = logger.error if resolved_status >= 500 else logger.warning
    log(
        "Domain exception reached global handler",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "exception_type": type(exc).__name__,
            "status_code": resolved_status,
        },
        exc_info=resolved_status >= 500,
    )
    return JSONResponse(
        status_code=resolved_status,
        content=_error_content(detail=str(exc), error_code=exc.error_code, request_id=request_id),
        headers=_retry_after_headers(exc) or None,
    )


async def _on_unhandled_integrity_error(
    request: Request,
    exc: IntegrityError,
) -> JSONResponse:
    """Hide SQL details while preserving the actionable constraint name."""
    request_id = getattr(request.state, "request_id", None)
    constraint_name = getattr(getattr(exc, "orig", None), "constraint_name", None)
    logger.exception(
        "Unhandled database constraint violation",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "constraint": constraint_name,
        },
    )
    detail = (
        f"Request violates database constraint {constraint_name!r}."
        if constraint_name
        else "Request violates a database constraint."
    )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_content(
            detail=detail,
            error_code="DATABASE_CONSTRAINT_VIOLATION",
            request_id=request_id,
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install specific handlers; the untyped catch-all remains middleware."""
    app.add_exception_handler(LLMServiceError, _on_unhandled_llm_service_error)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, _on_unhandled_integrity_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _on_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _on_request_validation_error)  # type: ignore[arg-type]
