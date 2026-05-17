"""
app/providers/error_classifier.py — Maps raw provider errors to LLMServiceError hierarchy.

Since we use direct HTTP requests (via httpx) for most providers and aioboto3 for Bedrock,
this module categorizes raw HTTP status codes and aioboto3 exceptions into our
internal ProviderError subclasses.
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
        so the response attribute is never accessed in practice — but it is declared
        here so static analysis does not flag attribute access on the class.
        """

        response: ClassVar[dict[str, object]] = {}

    ClientError = ConnectTimeoutError = ReadTimeoutError = _BotocoreStub  # type: ignore[assignment]


def classify_error(exc: Exception, provider_name: str) -> ProviderError:
    """Classify a raw exception from the provider into the ProviderError hierarchy.

    Args:
        exc: The raw exception caught from httpx or aioboto3.
        provider_name: The name of the provider.

    Returns:
        An instantiated subclass of ProviderError.
    """
    if isinstance(exc, httpx.TimeoutException):
        # We don't have the exact configured timeout value here, so default to 0.0
        # or it could be extracted from exc if httpx exposes it.
        return ProviderTimeoutError(provider_name=provider_name, timeout_seconds=0.0)

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text.lower()

        if status == 401:
            if "expired" in body:
                return ExpiredTokenError(provider_name=provider_name)
            return InvalidAPIKeyError(provider_name=provider_name, masked_key="****")

        if status == 429:
            # Simple heuristic; specific providers may need custom parsing
            # to extract exact retry_after_seconds.
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
                # Extracting requested model would require access to the request,
                # so we use a placeholder "unknown" or extract from body.
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

    # AWS Bedrock handling
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

    # Fallback
    return ProviderInternalError(
        f"Unhandled exception communicating with {provider_name}: {exc.__class__.__name__}",
        provider_name=provider_name,
        details={"raw_error": str(exc)},
    )
