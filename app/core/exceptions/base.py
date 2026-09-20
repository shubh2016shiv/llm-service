"""Base exception contract shared by every service error.

Architecture: all themed exception modules -> LLMServiceError -> API handlers.
"""


class LLMServiceError(Exception):
    """Base exception for every typed error raised by this service.

    Algorithm: retain a stable machine-readable code plus optional structured
    diagnostic details while preserving normal Python exception behavior.
    """

    error_code: str = "LLM_SERVICE_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        """Initialize the exception with safe diagnostic context."""
        super().__init__(message)
        self.details: dict[str, object] = details or {}
