"""Security and robustness tests for provider error normalization."""

from __future__ import annotations

import httpx

from app.core.exceptions import RequestsPerMinuteExceededError
from app.providers.http_errors import classify_error


def test_classify_error_with_http_date_retry_after_does_not_raise() -> None:
    """A valid HTTP-date Retry-After value safely degrades when only seconds are supported."""
    request = httpx.Request("POST", "https://provider.example/chat")
    response = httpx.Response(
        429,
        headers={"Retry-After": "Wed, 21 Oct 2030 07:28:00 GMT"},
        request=request,
    )
    error = httpx.HTTPStatusError("throttled", request=request, response=response)

    result = classify_error(error, "example")

    assert isinstance(result, RequestsPerMinuteExceededError)
    assert result.retry_after_seconds is None


def test_classify_unknown_error_does_not_echo_raw_exception_text() -> None:
    """Secrets embedded in SDK exception messages never enter response metadata."""
    result = classify_error(RuntimeError("token=super-secret"), "example")

    assert "super-secret" not in str(result)
    assert result.details == {"exception_type": "RuntimeError"}
