"""Streaming capacity failures exposed through the application error contract.

Architecture:
    stream_capacity.py -> StreamCapacityExceededError -> API exception handler

The exception lives in ``core.exceptions`` because capacity policy and its HTTP
translation are application concerns. The reusable SSE protocol modules do not
depend on this service's exception hierarchy.
"""

from app.core.exceptions.base import LLMServiceError
from app.core.exceptions.llm_provider import ProviderUnavailableError


class StreamDurationExceededError(LLMServiceError):
    """Raised after an open stream reaches its absolute lifetime limit."""

    error_code = "STREAM_DURATION_EXCEEDED"

    def __init__(self) -> None:
        """Create a safe post-header failure without exposing timing internals."""
        super().__init__("The stream reached its maximum allowed duration.")


class StreamCapacityExceededError(ProviderUnavailableError):
    """Raised when one worker has no safe capacity for another open stream."""

    error_code = "STREAM_CAPACITY_EXCEEDED"

    def __init__(self, limit: int, retry_after_seconds: int) -> None:
        """Create a retryable capacity error without queueing the connection."""
        super().__init__(
            f"Streaming capacity is temporarily full (limit={limit}).",
            provider_name="streaming",
            status_code=503,
        )
        self.retry_after_seconds = retry_after_seconds
