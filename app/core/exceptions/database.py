"""Database availability exceptions.

Architecture: PostgreSQL session provider -> database error -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class DatabaseUnavailableError(LLMServiceError):
    """PostgreSQL could not be reached, or dropped the connection mid-request.

    Distinct from errors where the database answered and refused the work — a
    constraint violation or a malformed statement. Those mean the request or
    this service's code is wrong and a retry will never fix them, so they keep
    their 400/409/500 answers. This one means no answer arrived at all: the
    server is unreachable, the pool is exhausted, or the connection dropped.

    The distinction earns its own type because the remediation differs. Callers
    are told 503 and may retry; operators are pointed at the database rather
    than at this service's own code.
    """

    error_code = "DATABASE_UNAVAILABLE"

    def __init__(self, operation: str) -> None:
        """Initialize with the failed operation, never the connection URL.

        The message reaches the client verbatim (the global handler responds
        with ``detail=str(exc)``), so callers pass a short operation label —
        never the SQLAlchemy error, which would echo the SQL statement, and
        never the database URL, which is a secret. The underlying failure
        travels on the ``from exc`` chain for the logs instead.
        """
        super().__init__(
            f"Database is unavailable while completing operation: {operation}",
            details={"operation": operation},
        )
        self.operation = operation
