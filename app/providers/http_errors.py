"""
Provider Error Classification
=============================

Maps raw transport/SDK exceptions into canonical domain-level provider errors.

Why this module exists:
    - Each provider and SDK emits different exception types and payload shapes.
    - Upstream layers should not branch on httpx, otocore, or provider-specific
      error formats.
    - A normalized error surface keeps retry, alerting, and HTTP translation stable.

Rationale:
    - Classification is best-effort and conservative: unknown failures become
      ProviderInternalError with safe diagnostic metadata.
    - Retry-oriented and quota-oriented errors are mapped explicitly so caller-side
      behavior (for example Retry-After) can remain deterministic.

Enterprise Pattern: Anti-Corruption Error Boundary

Author: Shubham Singh
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from app.core.exceptions import (
    ExpiredTokenError,
    InvalidAPIKeyError,
    InvalidRequestError,
    ModelNotSupportedError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    RequestsPerMinuteExceededError,
    ServiceDownError,
    TokensPerMinuteExceededError,
)

# Optional boto3 support
try:
    from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
except ImportError:

    class _BotocoreStub(Exception):
        """Placeholder used when botocore is not installed.

        isinstance checks against this class are always False for real AWS errors,
        so the response attribute is never accessed in practice.
        """

        response: ClassVar[dict[str, object]] = {}

    ClientError = ConnectTimeoutError = ReadTimeoutError = _BotocoreStub  # type: ignore[assignment]


def classify_error(exc: Exception, provider_name: str) -> ProviderError:
    """Classify a raw exception from the provider into the ProviderError hierarchy.

    Args:
        exc: The raw exception caught from httpx or aioboto3.
        provider_name: The name of the provider (used in exception messages).

    Returns:
        An instantiated subclass of ProviderError — never raises.
    """
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(provider_name=provider_name, timeout_seconds=0.0)

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text.lower()

        if status == 401:
            if "expired" in body:
                return ExpiredTokenError(provider_name=provider_name)
            return InvalidAPIKeyError(provider_name=provider_name, masked_key="****")

        if status == 429:
            retry_after = int(exc.response.headers.get("Retry-After", 0)) or None
            if "token" in body:
                return TokensPerMinuteExceededError(
                    provider_name=provider_name, retry_after_seconds=retry_after
                )
            return RequestsPerMinuteExceededError(
                provider_name=provider_name, retry_after_seconds=retry_after
            )

        if status == 400:
            if "model" in body and "not found" in body:
                return ModelNotSupportedError(provider_name=provider_name, model_name="unknown")
            return InvalidRequestError(
                provider_name=provider_name, field="unknown", reason=body[:200]
            )

        if status in (500, 502, 503, 504):
            return ServiceDownError(provider_name=provider_name, status_code=status)

        return ProviderInternalError(
            f"Unexpected HTTP status {status} from {provider_name}",
            provider_name=provider_name,
            status_code=status,
        )

    if isinstance(exc, httpx.RequestError):
        return ServiceDownError(provider_name=provider_name, status_code=503)

    # AWS Bedrock
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError)):
        return ProviderTimeoutError(provider_name=provider_name, timeout_seconds=0.0)

    if isinstance(exc, ClientError):
        error_block = exc.response.get("Error", {})
        error_block = error_block if isinstance(error_block, dict) else {}
        code: str = error_block.get("Code", "Unknown")
        msg: str = error_block.get("Message", "")

        if code in (
            "AccessDeniedException",
            "UnrecognizedClientException",
            "InvalidSignatureException",
        ):
            return InvalidAPIKeyError(provider_name=provider_name, masked_key="****")
        if code == "ThrottlingException":
            return RequestsPerMinuteExceededError(provider_name=provider_name)
        if code == "ValidationException":
            return InvalidRequestError(provider_name=provider_name, field="unknown", reason=msg)
        if code == "ResourceNotFoundException":
            return ModelNotSupportedError(provider_name=provider_name, model_name="unknown")
        if code in ("InternalServerException", "ServiceUnavailableException"):
            return ServiceDownError(provider_name=provider_name, status_code=503)

    return ProviderInternalError(
        f"Unhandled exception communicating with {provider_name}: {exc.__class__.__name__}",
        provider_name=provider_name,
        details={"raw_error": str(exc)},
    )

