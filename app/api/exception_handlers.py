"""
app/api/exception_handlers.py — Converts business errors into HTTP responses.

TL;DR for new developers:
    When something goes wrong in the business logic (e.g., a tenant is
    suspended, a rate limit is hit, a deployment is not found), the service
    layer raises a domain exception — an error object that describes the
    problem in business terms. This module is the single place that translates
    those domain exceptions into the correct HTTP status code (400, 403, 404,
    429, 500, etc.) and sends it back to the client. No other file decides
    what HTTP status an error gets.

Enterprise Pattern: Exception Translation Layer Pattern
    Domain exceptions (business-meaningful errors) and HTTP responses (network-
    protocol concerns) are kept separate. This module bridges them in one
    central place. Adding a new error type means adding one mapping entry here
    — route handlers never need to know or duplicate status-code logic.

Public surface
--------------
    translate_inference_error(exc)   — called by inference route handlers
    translate_management_error(exc)  — called by management route handlers
    register_exception_handlers(app) — called once in main.py's lifespan setup

Design contract
---------------
    Route handlers are responsible for logging domain failures WITH their own
    request context (tenant_id, deployment_key, resource_id, etc.) BEFORE
    calling a translate function. The translate functions do not duplicate that
    log — they only raise the mapped HTTPException.

    The EXCEPTION to that rule is translate_management_error, which centralises
    the log because management handlers have no per-route context worth adding.

    The global handler (registered via register_exception_handlers) is a pure
    safety net. Any LLMServiceError that escapes all route-level handlers lands
    here. If it fires, that is a gap in the route-level handling — treat the log
    entry as a bug report.

How exception-to-status resolution works
-----------------------------------------
    All three entry points share the same resolution logic: walk the
    exception's class hierarchy (its parent classes, grandparents, etc.)
    until a matching rule is found. This guarantees the most specific
    subclass wins without requiring the caller to order exception checks
    manually.

    Example — given the hierarchy:
        RateLimitError → ProviderError → LLMServiceError
    A RequestsPerMinuteExceededError (a child of RateLimitError) that has
    no direct mapping entry will still resolve to HTTP 429 via its parent
    RateLimitError, before falling through to the generic HTTP 500.

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
# Inference exception → HTTP status mapping
#
# Covers every domain exception that can surface from the inference path:
# auth dependency → resolution pipeline → quota check → provider call.
#
# Ordering rationale for ambiguous cases:
#   AuthenticationError → 502 (Bad Gateway): the provider rejected OUR stored
#     credential. The caller supplied a valid JWT; this is a server-side config
#     failure, not a client auth failure (which would be 401).
#
#   ProviderValidationError → 422: the provider rejected the request body. The
#     request was well-formed at our API boundary but invalid for the provider.
#
#   ProviderUnavailableError → 503: provider is down or unreachable. The caller
#     should retry after a delay (per Retry-After if present).
#
#   ProviderTimeoutError → 504: provider did not respond in time. Distinct from
#     503 because the connection was established but the response never arrived.
#
#   ProviderNotAllowedError → 403: the tenant deployment config does not permit
#     this provider. The caller is authenticated but the route is forbidden.
#
#   OperationNotSupportedError → 422: the operation (embed, rerank) is not
#     supported by the resolved model. The request is semantically invalid for
#     this deployment, not a permissions issue.
# ---------------------------------------------------------------------------

_INFERENCE_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    # Tenant / deployment resolution errors (set by the auth dependency)
    TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantSuspendedError: status.HTTP_403_FORBIDDEN,
    DeploymentNotFoundError: status.HTTP_404_NOT_FOUND,
    DeploymentInactiveError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    # Quota and concurrency
    QuotaExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ConcurrentRequestLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    # Rate limits from the provider (subclasses also resolve here via MRO)
    RateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    # Provider auth — 502 not 401: our stored credential is wrong, not the caller's
    AuthenticationError: status.HTTP_502_BAD_GATEWAY,
    # Provider validation — request was valid at our boundary, rejected by provider
    ProviderValidationError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    # Routing pipeline — tenant config does not allow this provider/operation
    ProviderNotAllowedError: status.HTTP_403_FORBIDDEN,
    OperationNotSupportedError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    # Provider availability
    ProviderUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ProviderTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}


# ---------------------------------------------------------------------------
# Management exception → HTTP status mapping
#
# Covers every domain exception raised by the management services layer.
#
# Ordering rationale for ambiguous cases:
#   TenantAccessDeniedError → 403: the caller is authenticated but lacks the
#     required tenant-scoped role. Mapped before its parent ManagementError.
#
#   InvalidStateTransitionError → 409: the resource exists but its current
#     lifecycle state prevents the requested transition (e.g., activating an
#     already-active deployment). RFC 7231 §6.5.8 — "conflict with the current
#     state of the target resource" — fits better than 400 (bad request syntax)
#     or 422 (unprocessable). Must appear before ManagementError in the MRO walk.
#
#   ResourceConflictError → 409: unique constraint violation (duplicate name,
#     duplicate key). RFC 7231 §6.5.8 applies here too.
#
#   ManagementValidationError → 400: domain-level validation failure (e.g.,
#     referencing a non-existent provider UUID). The request was syntactically
#     valid but semantically rejected by business rules.
#
#   ManagementError → 400: catch-all for any ManagementError subclass that has
#     no more specific entry above. Must be last in the class hierarchy so it
#     never shadows its subclasses.
# ---------------------------------------------------------------------------

_MANAGEMENT_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    ResourceNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantAccessDeniedError: status.HTTP_403_FORBIDDEN,
    # State transition and uniqueness conflicts before generic ManagementError
    InvalidStateTransitionError: status.HTTP_409_CONFLICT,
    ResourceConflictError: status.HTTP_409_CONFLICT,
    ManagementValidationError: status.HTTP_400_BAD_REQUEST,
    # Catch-all for any ManagementError subclass not listed above
    ManagementError: status.HTTP_400_BAD_REQUEST,
}


# ---------------------------------------------------------------------------
# Fallback exception → HTTP status mapping
#
# Used by the global safety-net handler registered on the FastAPI app.
# This handler only fires when a domain exception escaped BOTH the inference
# and management route-level handlers — which should never happen in normal
# operation. This mapping covers the full exception surface as a union.
#
# If a LLMServiceError subtype is not present here, _resolve_status returns
# HTTP 500 so the caller always gets a structured JSON response.
# ---------------------------------------------------------------------------

_FALLBACK_EXCEPTION_STATUS: dict[type[LLMServiceError], int] = {
    **_MANAGEMENT_EXCEPTION_STATUS,
    # Inference-path exceptions that would not normally escape to this handler
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


# ---------------------------------------------------------------------------
# Shared MRO-walk resolution helper (private)
#
# Walks type(exc).__mro__ until a mapping entry is found. The most specific
# subclass wins because Python's MRO always lists concrete types before their
# bases. If no entry is found, returns `fallback_status`.
#
# Why MRO walk instead of isinstance chain?
#   - Adding a new exception subclass does not require touching this file as
#     long as its parent is already mapped.
#   - The walk is O(depth of inheritance tree) — negligible for our hierarchy.
#   - It avoids the subtle ordering bugs that arise with a chain of isinstance
#     checks where the wrong branch can shadow the intended handler.
# ---------------------------------------------------------------------------


def _resolve_status(
    exc: LLMServiceError,
    mapping: dict[type[LLMServiceError], int],
    fallback_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> int:
    """Return the HTTP status code for `exc` by walking its MRO against `mapping`."""
    for exc_type in type(exc).__mro__:
        if exc_type in mapping:
            return mapping[exc_type]  # type: ignore[index]
    return fallback_status


# ---------------------------------------------------------------------------
# Retry-After header helper (private)
#
# RFC 6585 §4 states that 429 responses SHOULD include a Retry-After header.
# RateLimitError subclasses optionally carry retry_after_seconds populated
# from the provider response's Retry-After header. We propagate it here so
# callers (and any upstream gateway) know when to retry.
#
# We only set the header when the value is known — we never invent a delay.
# ---------------------------------------------------------------------------


def _retry_after_headers(exc: LLMServiceError) -> dict[str, str]:
    """Return a Retry-After header dict if the exception carries a retry hint."""
    retry_seconds: int | None = getattr(exc, "retry_after_seconds", None)
    if retry_seconds is not None:
        return {"Retry-After": str(retry_seconds)}
    return {}


# ---------------------------------------------------------------------------
# translate_inference_error
#
# Called by inference route handlers after catching LLMServiceError.
# The CALLER is responsible for logging with tenant/deployment context before
# calling this — do not add logging here.
#
# Usage:
#     except LLMServiceError as exc:
#         logger.warning("...", tenant_id, deployment_key, exc.error_code)
#         translate_inference_error(exc)
# ---------------------------------------------------------------------------


def translate_inference_error(exc: LLMServiceError) -> NoReturn:
    """Translate an inference-path domain exception to an HTTPException and raise it.

    Args:
        exc: A domain exception from the inference path.

    Raises:
        HTTPException: Always. Status code is resolved by MRO walk against the
            inference exception map. Falls back to HTTP 500.
    """
    resolved_status = _resolve_status(exc, _INFERENCE_EXCEPTION_STATUS)
    raise HTTPException(
        status_code=resolved_status,
        detail=str(exc),
        # Propagate provider-supplied Retry-After to the caller for 429 responses.
        headers=_retry_after_headers(exc) or None,
    )


# ---------------------------------------------------------------------------
# translate_management_error
#
# Called by management route handlers after catching LLMServiceError.
# Unlike translate_inference_error, this function centralises logging because
# management handlers carry no per-route inference context. The structured
# log entry includes the request_id ContextVar so it correlates with the
# middleware log for the same request.
#
# Usage:
#     except LLMServiceError as exc:
#         translate_management_error(exc)   # logs + raises
# ---------------------------------------------------------------------------


def translate_management_error(exc: LLMServiceError) -> NoReturn:
    """Translate a management-path domain exception to an HTTPException and raise it.

    Logs a structured WARNING before raising so every managed failure produces
    a correlated log entry, even if the calling handler has no per-entity context.

    Args:
        exc: A domain exception from the management services layer.

    Raises:
        HTTPException: Always. Status code is resolved by MRO walk against the
            management exception map. Falls back to HTTP 500.
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


# ---------------------------------------------------------------------------
# Global safety-net handler
#
# Catches any LLMServiceError that escaped ALL route-level handlers. In normal
# operation this should never fire — route handlers call translate_inference_error
# or translate_management_error before any domain exception can propagate here.
#
# When it DOES fire, it means a route handler is missing an except clause. The
# log entry is deliberately verbose (ERROR for 5xx, WARNING for 4xx) so the gap
# is easy to find in production logs.
#
# Registered on the FastAPI app by register_exception_handlers() below.
# ---------------------------------------------------------------------------


async def _on_unhandled_llm_service_error(request: Request, exc: LLMServiceError) -> JSONResponse:
    """Safety-net: convert any escaped LLMServiceError to a structured JSON response.

    This handler fires only when a domain exception was NOT caught by any
    route-level handler. The response format mirrors FastAPI's built-in error
    shape — {"detail": "..."} — so clients have a consistent contract even in
    the unexpected path.

    Args:
        request: The FastAPI request that produced the exception.
        exc: The escaped domain exception.

    Returns:
        A JSONResponse with an appropriate status code and detail message.
    """
    resolved_status = _resolve_status(exc, _FALLBACK_EXCEPTION_STATUS)

    # Distinguish genuine server faults (5xx) from domain errors (4xx).
    # A 4xx here means a route handler silently swallowed the exception instead
    # of translating it — still a gap, but less severe than an unhandled 5xx.
    if resolved_status >= 500:
        logger.error(
            "Unhandled domain exception reached global handler — route handler gap | "
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


# ---------------------------------------------------------------------------
# Handler registration
#
# Called once during app startup in main.py.
# Keeping registration here (rather than in main.py) means main.py only does
# wiring and this module owns the full exception-handling contract.
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI application.

    Must be called after the app object is created and before it starts
    accepting traffic. In practice, call this at the end of main.py, after
    `app = FastAPI(...)`.

    Args:
        app: The FastAPI application instance.
    """
    # Global fallback: catches any LLMServiceError that escaped route handlers.
    # This is a last-resort guardrail — route handlers should never let domain
    # exceptions reach this point in normal operation.
    app.add_exception_handler(LLMServiceError, _on_unhandled_llm_service_error)  # type: ignore[arg-type]
