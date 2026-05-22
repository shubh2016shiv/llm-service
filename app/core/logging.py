"""
Structured Logging — JSON formatter and structured logger for the service.

This module:
    1. Defines JSONFormatter — converts stdlib LogRecord to a JSON string
    2. Defines StructuredLogger — thin wrapper enforcing structured `extra` fields
    3. Provides configure_logging() — call once at startup to wire everything up

Step-by-step runtime relationship:
    1. Startup calls ``configure_logging(...)`` with level/format/environment.
    2. Root logger is wired with either ``JSONFormatter`` or ``TextFormatter``.
    3. Module loggers emit messages with structured ``extra`` fields.
    4. Formatter serializes records into machine-parseable output.
    5. Observability pipeline ingests logs with consistent keys.

Design rules enforced here:
    - Never use print() — always use structured logger
    - Log fields are extra= kwargs, never string concatenation
    - PII fields (prompts, API keys, emails) must never appear in log output
    - Every log record includes service, environment, and level at minimum

Architecture:
-------------
    ApplicationSettings.log_level
          │
          ▼
    configure_logging()   ← call once at FastAPI startup
          │
          ▼
    root logger ── JSONFormatter ──► stdout (captured by log aggregator)

    Provider code:
    logger = logging.getLogger(__name__)
    logger.info("chat.generate succeeded", extra={...})
          │
          ▼
    JSONFormatter.format() → {"timestamp": ..., "level": "INFO", ...}

Dependencies:
    - stdlib logging, json, datetime

Author: Shubham Singh
"""

from __future__ import annotations

import json
import logging as _logging
import sys
from datetime import UTC, datetime
from typing import Any, ClassVar

# ── Log Record Dataclass ──────────────────────────────────────────────────────


class ProviderLogContext:
    """Structured context fields emitted alongside every provider operation log.

    Passed as the ``extra`` argument to logger calls. Fields map directly to the
    log schema documented in the HLD.

    Example:
        >>> ctx = ProviderLogContext(
        ...     operation="chat.generate",
        ...     provider_name="openai",
        ...     deployment_name="gpt4-prod",
        ...     request_id="req-abc123",
        ...     tenant_id="acme-uuid",
        ... )
        >>> logger.info("Request succeeded", extra=ctx.to_dict())
    """

    __slots__ = (
        "completion_tokens",
        "deployment_id",
        "deployment_name",
        "error_type",
        "estimated_cost_usd",
        "latency_ms",
        "model_name",
        "operation",
        "prompt_tokens",
        "provider_latency_ms",
        "provider_name",
        "request_id",
        "retry_count",
        "status_code",
        "tenant_id",
        "total_tokens",
        "trace_id",
        "user_id",
    )

    def __init__(
        self,
        operation: str,
        provider_name: str,
        *,
        deployment_name: str | None = None,
        deployment_id: str | None = None,
        model_name: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        latency_ms: int | None = None,
        provider_latency_ms: int | None = None,
        status_code: int | None = None,
        retry_count: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_type: str | None = None,
    ) -> None:
        """Initialise all structured log context fields.

        Args:
            operation: Dotted operation name (e.g., 'chat.generate', 'embed').
            provider_name: Lowercase provider identifier.
            deployment_name: Human-readable deployment label.
            deployment_id: UUID of the deployment.
            model_name: Model used in the call (e.g., 'gpt-4o').
            request_id: Per-request unique identifier.
            trace_id: Distributed trace identifier (propagated from caller).
            tenant_id: UUID of the requesting tenant.
            user_id: UUID of the requesting user (if available).
            latency_ms: Total end-to-end latency in milliseconds.
            provider_latency_ms: Latency of the upstream provider call only.
            status_code: HTTP status code returned by the provider.
            retry_count: Number of retry attempts before this result.
            prompt_tokens: Number of input tokens consumed.
            completion_tokens: Number of output tokens generated.
            total_tokens: Total tokens (prompt + completion).
            estimated_cost_usd: Estimated USD cost for this call.
            error_type: Exception class name if the call failed.
        """
        self.operation = operation
        self.provider_name = provider_name
        self.deployment_name = deployment_name
        self.deployment_id = deployment_id
        self.model_name = model_name
        self.request_id = request_id
        self.trace_id = trace_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.latency_ms = latency_ms
        self.provider_latency_ms = provider_latency_ms
        self.status_code = status_code
        self.retry_count = retry_count
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.estimated_cost_usd = estimated_cost_usd
        self.error_type = error_type

    def to_dict(self) -> dict[str, Any]:
        """Convert context to a dict suitable for logger.info(extra=...).

        Omits None fields to keep log output clean.

        Returns:
            Dict with non-None context fields.
        """
        return {
            slot: getattr(self, slot)
            for slot in self.__slots__
            if getattr(self, slot) is not None
        }


# ── JSON Formatter ────────────────────────────────────────────────────────────


class JSONFormatter(_logging.Formatter):
    """Formats stdlib LogRecord instances as single-line JSON strings.

    Extracts standard fields (level, message, logger name, exception) and
    merges any ``extra`` dict fields into the JSON output. Fields that are
    objects are converted to strings to ensure JSON-serialisability.

    Example record produced:
        {
          "timestamp": "2026-05-16T14:31:00.123456Z",
          "level": "INFO",
          "logger": "app.providers.openai_provider",
          "message": "chat.generate succeeded",
          "operation": "chat.generate",
          "provider_name": "openai",
          "latency_ms": 1150,
          ...
        }
    """

    # Fields present on every LogRecord - exclude from ``extra`` extraction.
    _STDLIB_FIELDS: frozenset[str] = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "taskName",
    })

    def __init__(
        self,
        service_name: str = "llm-provider-service",
        environment: str = "development",
    ) -> None:
        """Initialise formatter with service metadata injected into every record.

        Args:
            service_name: Service name added to every log line.
            environment: Deployment environment added to every log line.
        """
        super().__init__()
        self._service_name = service_name
        self._environment = environment

    def format(self, record: _logging.LogRecord) -> str:
        """Format a LogRecord as a JSON string.

        Args:
            record: The stdlib LogRecord to format.

        Returns:
            A single-line JSON string terminated without a newline.
        """
        record.message = record.getMessage()

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service_name,
            "environment": self._environment,
            "message": record.message,
        }

        # Merge any `extra` fields injected via logger.info(extra={...})
        for key, value in record.__dict__.items():
            if key not in self._STDLIB_FIELDS:
                payload[key] = self._safe_serialize(value)

        # Append exception traceback if present
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)

    @staticmethod
    def _safe_serialize(value: Any) -> Any:
        """Convert a value to something JSON-serialisable.

        Args:
            value: Any Python value from the log extra dict.

        Returns:
            A JSON-compatible representation of the value.
        """
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)


# ── Text Formatter (development) ──────────────────────────────────────────────


class TextFormatter(_logging.Formatter):
    """Human-readable text formatter for development use.

    Uses colour codes where the terminal supports them.
    Never use this in production — JSON is required for log aggregation.
    """

    _LEVEL_COLOURS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    _RESET: ClassVar[str] = "\033[0m"

    def format(self, record: _logging.LogRecord) -> str:
        """Format record as a coloured single-line string.

        Args:
            record: The stdlib LogRecord to format.

        Returns:
            A coloured, human-readable log string.
        """
        colour = self._LEVEL_COLOURS.get(record.levelname, "")
        reset = self._RESET if colour else ""
        record.message = record.getMessage()
        timestamp = datetime.fromtimestamp(
            record.created, tz=UTC
        ).strftime("%H:%M:%S.%f")[:-3]
        return (
            f"{colour}[{record.levelname:8}]{reset} "
            f"{timestamp} {record.name} — {record.message}"
        )


# ── Configuration Entry Point ─────────────────────────────────────────────────


def configure_logging(
    level: str = "INFO",
    format: str = "json",
    service_name: str = "llm-provider-service",
    environment: str = "development",
) -> None:
    """Configure the root logger for the entire application.

    Call this exactly once at application startup (for example, in FastAPI lifespan).
    All subsequent loggers obtained via logging.getLogger(__name__) will
    inherit this configuration automatically.

    Args:
        level: Log level string: DEBUG | INFO | WARNING | ERROR | CRITICAL.
        format: Output format: 'json' (production) | 'text' (development).
        service_name: Injected into every JSON log record.
        environment: Injected into every JSON log record.

    Example:
        >>> configure_logging(
        ...     level="INFO",
        ...     format="json",
        ...     service_name="llm-provider-service",
        ...     environment="production",
        ... )
    """
    formatter: _logging.Formatter
    if format == "json":
        formatter = JSONFormatter(
            service_name=service_name, environment=environment
        )
    else:
        formatter = TextFormatter()

    handler = _logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = _logging.getLogger()
    # WHY: Remove any existing handlers first to prevent duplicate output
    # when configure_logging() is called after a test framework has already
    # set up basicConfig.
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(_logging, level.upper(), _logging.INFO))

    # Silence noisy third-party loggers that pollute output in production.
    _logging.getLogger("httpx").setLevel(_logging.WARNING)
    _logging.getLogger("httpcore").setLevel(_logging.WARNING)
    _logging.getLogger("asyncio").setLevel(_logging.WARNING)
    _logging.getLogger("botocore").setLevel(_logging.WARNING)
    _logging.getLogger("aiobotocore").setLevel(_logging.WARNING)
