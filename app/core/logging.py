"""
Structured Logging — portable, project-agnostic JSON logging.

A single drop-in file: copy it into any project and call ``configure_logging()``
once at startup. Nothing here is domain-specific — projects supply their own
vocabulary through ``extra={...}`` and ``log_context(...)``.

Provides:
    log_context()       — ambient, request-scoped fields via contextvars
    RedactFilter        — enforced secret/PII redaction, recursive
    JSONFormatter       — structure-preserving single-line JSON
    TextFormatter       — human-readable coloured output for local dev
    configure_logging() — the one call that wires it all together

Guarantees enforced in code, not merely documented:
    - Nested dicts/lists in ``extra`` stay JSON objects/arrays, never
      stringified, so they remain queryable downstream.
    - Core fields (timestamp, level, logger, service, environment, message)
      cannot be clobbered by a caller's ``extra``; collisions are prefixed.
    - Redaction walks nested structures, so burying a credential one level
      down does not leak it.
    - Correlation IDs propagate across ``await`` points.
    - A logging call never raises and never loses a line.

Architecture:
-------------
    ApplicationSettings.log_level
          │
          ▼
    configure_logging()   ← call once at FastAPI startup
          │
          ▼
    root logger ─┬─ _ContextInjectingFilter  (adds ambient log_context fields)
                 ├─ RedactFilter             (masks secrets, recursively)
                 └─ JSONFormatter/TextFormatter ──► stdout ──► log aggregator

    Call site:
        with log_context(request_id=rid):
            logger.info("chat.generate ok", extra={"latency_ms": 1150})
        ──► {"timestamp":…,"level":"INFO","request_id":…,"latency_ms":1150}

Project-specific fields: declare a small dataclass next to the code that uses
it and pass ``extra=ctx.to_extra()``. WHY this file no longer defines
``ProviderLogContext``: a reusable logging core that knows about LLM providers
is not reusable — the domain owns its schema, the transport stays generic.

``Any`` usage (AGENTS.md §4.1): log values are by definition whatever the call
site chose to log, so constraining ``extra`` would defeat the module's purpose.
Every such value passes through ``RedactFilter``/``_json_default``, which treat
unknown types defensively rather than trusting them.

Dependencies:
    - stdlib only: contextvars, json, logging, os, sys, datetime, decimal, uuid

Author: Shubham Singh
Last Updated: 2026-08-22
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar, Final
from uuid import UUID

__all__ = [
    "DEFAULT_HANDOFF_LOGGERS",
    "DEFAULT_QUIET_LOGGERS",
    "DEFAULT_REDACT_KEYS",
    "JSONFormatter",
    "RedactFilter",
    "TextFormatter",
    "configure_logging",
    "get_context",
    "get_logger",
    "log_context",
]


# ── Ambient Request Context ───────────────────────────────────────────────────
#
# PATTERN: ambient context via contextvars.
# WHY: correlation IDs are cross-cutting. Threading a request_id through every
# signature pollutes APIs that have no business knowing about logging, and a
# module-level dict would leak between concurrent requests. A ContextVar is
# isolated per-task and copied across ``await`` points and into threads started
# by ``asyncio.to_thread``, so the ID follows the logical unit of work.

# WHY an immutable default: a plain ``{}`` default is shared by every context
# that never sets a value, so an accidental in-place mutation would bleed across
# requests. MappingProxyType makes that mistake raise instead.
_context_var: contextvars.ContextVar[Mapping[str, Any]] = contextvars.ContextVar(
    "_structured_log_context",
    default=MappingProxyType({}),
)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Merge fields into the ambient log context for the life of this block.

    Every record emitted inside the block carries these fields, including
    records from code called within it and coroutines awaited inside it. Nested
    blocks inherit and override outer fields.

    Args:
        **fields: Key/value pairs to attach to enclosed log records. Prefer
            unambiguous names (``request_id``, not ``name``); keys colliding
            with a stdlib LogRecord attribute are dropped.

    Yields:
        None. The context is restored on exit, including on exception.

    Example:
        >>> with log_context(request_id="req-1", tenant_id="acme"):
        ...     logger.info("handling request")  # both fields auto-attached
    """
    parent = _context_var.get()
    token = _context_var.set({**parent, **fields})
    try:
        yield
    finally:
        _context_var.reset(token)


def get_context() -> dict[str, Any]:
    """Return a shallow copy of the ambient log context; mutating it is inert."""
    return dict(_context_var.get())


class _ContextInjectingFilter(logging.Filter):
    """Attaches ambient ``log_context()`` fields to every outgoing record.

    Runs before ``RedactFilter`` so context values face the same redaction
    rules as explicit ``extra`` fields.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Copy ambient context onto the record; always True (never drops)."""
        for key, value in _context_var.get().items():
            # WHY hasattr: an explicit `extra` is more specific than ambient
            # context and must win. This also protects stdlib attributes
            # (name, module, ...) from being shadowed.
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


# ── Secret / PII Redaction ────────────────────────────────────────────────────
#
# PATTERN: enforcement at the boundary, not at the call site.
# WHY: "never log secrets" as a code-review rule fails the first time someone
# logs a whole request object. Redacting inside the pipeline covers every call
# site by construction, including third-party libraries.

DEFAULT_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "credit_card",
        "id_token",
        "passwd",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_id",
        "set_cookie",
        "ssn",
        "token",
        "x-api-key",
    }
)

_REDACTED: Final[str] = "[REDACTED]"
_TRUNCATED: Final[str] = "[TRUNCATED]"

# WHY a depth cap: redaction recurses through caller-supplied structures. A cap
# bounds the cost of a pathological payload and terminates on self-referential
# objects. Anything deeper is replaced rather than passed through, because an
# unredacted deep value is a worse outcome than a lost one.
_MAX_REDACT_DEPTH: Final[int] = 6


class RedactFilter(logging.Filter):
    """Replaces the value of any field whose *name* matches a redact key.

    Matching is case-insensitive and recurses into nested dicts and sequences,
    so ``extra={"headers": {"Authorization": "Bearer x"}}`` is redacted as
    thoroughly as ``extra={"authorization": "Bearer x"}``.

    Limitation, stated explicitly: this matches key names only. A secret
    interpolated into the message string (``logger.info(f"key={k}")``) is not
    detected. If a project needs value-pattern scanning, add a second filter —
    do not weaken this one with heuristics that produce false positives.

    Example:
        >>> handler.addFilter(RedactFilter(DEFAULT_REDACT_KEYS | {"prompt"}))
    """

    def __init__(self, redact_keys: frozenset[str] = DEFAULT_REDACT_KEYS) -> None:
        """Initialise the filter.

        Args:
            redact_keys: Field names to redact, compared case-insensitively.
        """
        super().__init__()
        self._redact_keys = frozenset(key.lower() for key in redact_keys)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact matching fields on the record; always True (never drops)."""
        for key, value in list(record.__dict__.items()):
            if key in _STDLIB_FIELDS or key.startswith("_"):
                continue
            if key.lower() in self._redact_keys:
                setattr(record, key, _REDACTED)
            elif isinstance(value, (Mapping, list, tuple, set, frozenset)):
                # WHY a rebuilt copy rather than in-place mutation: the caller
                # still holds a reference to the dict it passed as `extra`.
                # Redacting in place would destroy application data, turning a
                # logging concern into a correctness bug.
                setattr(record, key, self._redact_value(value, depth=0))
        return True

    def _redact_value(self, value: Any, depth: int) -> Any:
        """Return a redacted copy of ``value``, recursing through containers."""
        if depth > _MAX_REDACT_DEPTH:
            return _TRUNCATED

        if isinstance(value, Mapping):
            return {
                key: (
                    _REDACTED
                    if isinstance(key, str) and key.lower() in self._redact_keys
                    else self._redact_value(item, depth + 1)
                )
                for key, item in value.items()
            }

        # WHY exclude str/bytes: they are Sequences, and recursing into a string
        # yields an infinite regress of one-character strings.
        if isinstance(value, (list, tuple, set, frozenset)) or (
            isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
        ):
            return [self._redact_value(item, depth + 1) for item in value]

        return value


# ── Shared Record Metadata ────────────────────────────────────────────────────

# Attributes present on every LogRecord. Anything *not* listed here was put on
# the record by a caller's `extra` or by _ContextInjectingFilter, and is
# therefore structured data we want in the output.
_STDLIB_FIELDS: frozenset[str] = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)  # fmt: skip

# Keys the formatter owns. A caller's `extra` cannot silently overwrite these —
# collisions are emitted with an ``extra_`` prefix, preserving the data without
# corrupting the log schema.
_RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "environment", "exception", "level", "logger", "message", "service",
        "stack_trace", "timestamp",
    }
)  # fmt: skip


def _iso_timestamp(created: float) -> str:
    """Render ``LogRecord.created`` as ``2026-08-22T14:31:00.123456Z``.

    WHY the trailing ``Z`` rather than ``+00:00``: it is what most log
    aggregators expect, and what this module's own schema documents.
    """
    return datetime.fromtimestamp(created, tz=UTC).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    """Return a JSON-compatible form of a leaf value json cannot encode.

    dict/list/str/int/float/bool/None are handled by ``json.dumps`` directly
    and never reach here — which is precisely why nested structure in
    ``extra`` survives instead of being stringified.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset)):
        return list(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    return str(value)


def _collect_extras(record: logging.LogRecord) -> Iterator[tuple[str, Any]]:
    """Yield ``(output_key, value)`` for caller-supplied fields on a record."""
    for key, value in record.__dict__.items():
        if key in _STDLIB_FIELDS or key.startswith("_"):
            continue
        yield (f"extra_{key}" if key in _RESERVED_KEYS else key), value


# ── JSON Formatter (production) ───────────────────────────────────────────────


class JSONFormatter(logging.Formatter):
    """Formats LogRecords as single-line JSON, preserving nested structure.

    Merges ``extra`` and ambient ``log_context()`` fields into the top level
    without flattening, so downstream queries such as
    ``usage.prompt_tokens > 1000`` work against the raw log stream.

    Example:
        {"timestamp":"2026-08-22T14:31:00.123456Z","level":"INFO",
         "logger":"app.providers.openai_provider","service":"llm-provider-service",
         "environment":"production","message":"chat.generate succeeded",
         "request_id":"req-abc123","usage":{"prompt_tokens":812},"latency_ms":1150}
    """

    def __init__(
        self,
        service_name: str = "app",
        environment: str = "development",
        *,
        include_source: bool = False,
    ) -> None:
        """Initialise the formatter with metadata injected into every record.

        Args:
            service_name: Service identifier added to every log line.
            environment: Deployment environment added to every log line.
            include_source: Emit file/line/function. Useful when debugging;
                costs bytes on every line, so it is off by default.
        """
        super().__init__()
        self._service_name = service_name
        self._environment = environment
        self._include_source = include_source

    def format(self, record: logging.LogRecord) -> str:
        """Format a LogRecord as a single-line JSON string.

        Args:
            record: The stdlib LogRecord to format.

        Returns:
            A single-line JSON string, without a trailing newline.
        """
        record.message = record.getMessage()

        payload: dict[str, Any] = {
            "timestamp": _iso_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service_name,
            "environment": self._environment,
            "message": record.message,
        }

        for key, value in _collect_extras(record):
            payload[key] = value  # left as-is: dicts/lists stay structured

        if self._include_source:
            payload["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        if record.exc_info and isinstance(record.exc_info, tuple):
            exc_type, exc_value, _ = record.exc_info
            # WHY a nested object rather than one blob of text: aggregators
            # group errors by type and message. A single pre-rendered traceback
            # string makes "how many TimeoutErrors this hour" unanswerable.
            payload["exception"] = {
                "type": exc_type.__name__ if exc_type is not None else None,
                "message": str(exc_value) if exc_value is not None else "",
                "stacktrace": self.formatException(record.exc_info),
            }

        if record.stack_info:
            payload["stack_trace"] = self.formatStack(record.stack_info)

        return self._dumps(payload, record)

    def _dumps(self, payload: dict[str, Any], record: logging.LogRecord) -> str:
        """Serialise the payload, falling back to a minimal valid record."""
        try:
            return json.dumps(
                payload, default=_json_default, ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            # WHY never re-raise: an exotic dict key in one `extra` must not
            # cost us the log line — losing the message is a worse failure than
            # losing its structured fields.
            return json.dumps(
                {
                    "timestamp": _iso_timestamp(record.created),
                    "level": record.levelname,
                    "logger": record.name,
                    "service": self._service_name,
                    "environment": self._environment,
                    "message": record.getMessage(),
                    "log_serialisation_error": f"{type(exc).__name__}: {exc}",
                },
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
            )


# ── Text Formatter (development) ──────────────────────────────────────────────


class TextFormatter(logging.Formatter):
    """Human-readable coloured formatter for local development.

    Never use this in production — log aggregators need JSON. Colour is emitted
    only for a TTY, so piping to a file stays clean.
    """

    _LEVEL_COLOURS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    _RESET: ClassVar[str] = "\033[0m"

    def __init__(self, *, use_colour: bool = True) -> None:
        """Initialise the formatter.

        Args:
            use_colour: Emit ANSI colour codes. Disable for non-TTY streams.
        """
        super().__init__()
        self._use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        """Format a record as a coloured, human-readable line.

        Args:
            record: The stdlib LogRecord to format.

        Returns:
            A single log line, with the traceback appended when present.
        """
        colour = self._LEVEL_COLOURS.get(record.levelname, "") if self._use_colour else ""
        reset = self._RESET if colour else ""
        record.message = record.getMessage()
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S.%f")[:-3]

        extras = dict(_collect_extras(record))
        rendered = (
            " " + " ".join(f"{key}={self._render(value)}" for key, value in sorted(extras.items()))
            if extras
            else ""
        )
        line = (
            f"{colour}[{record.levelname:8}]{reset} {timestamp} "
            f"{record.name} — {record.message}{rendered}"
        )

        # WHY append explicitly: the base Formatter.format() appends tracebacks,
        # but this override never calls it. Without these two blocks, dev mode —
        # the one place a stack trace matters most — silently drops it.
        if record.exc_info and isinstance(record.exc_info, tuple):
            line = f"{line}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"

        return line

    @staticmethod
    def _render(value: Any) -> str:
        """Render one extra value: JSON for containers, ``str()`` otherwise."""
        if isinstance(value, (Mapping, list, tuple, set)):
            with contextlib.suppress(TypeError, ValueError):
                return json.dumps(value, default=_json_default, separators=(",", ":"))
        return str(value)


# ── Configuration Entry Point ─────────────────────────────────────────────────

# Third-party loggers that are chatty at INFO/DEBUG and drown application logs.
DEFAULT_QUIET_LOGGERS: tuple[str, ...] = (
    "aiobotocore", "asyncio", "boto3", "botocore", "httpcore", "httpx", "urllib3",
)  # fmt: skip

# Loggers that install their own handlers and would otherwise bypass this
# configuration entirely. Clearing their handlers and re-enabling propagation
# routes their output through our formatter, so access logs are JSON too.
# Naming loggers a given project does not use is harmless.
DEFAULT_HANDOFF_LOGGERS: tuple[str, ...] = (
    "gunicorn", "gunicorn.access", "gunicorn.error",
    "uvicorn", "uvicorn.access", "uvicorn.error",
)  # fmt: skip


def _resolve_level(level: str | int) -> int:
    """Translate ``"INFO"`` / ``"20"`` / ``20`` into a level; INFO if unknown."""
    if isinstance(level, int):
        return level
    name = level.strip().upper()
    resolved = logging.getLevelNamesMapping().get(name)
    if resolved is not None:
        return resolved
    return int(name) if name.isdigit() else logging.INFO


def _force_utf8(stream: Any) -> None:
    """Switch a text stream to UTF-8, best effort.

    WHY: ``ensure_ascii=False`` keeps non-ASCII content readable, but on Windows
    the console defaults to cp1252, where one non-Latin-1 character raises
    UnicodeEncodeError from inside the logging handler.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    # Best effort by design: a detached or non-text stream keeps its own
    # encoding rather than failing application startup over log cosmetics.
    with contextlib.suppress(ValueError, OSError):
        reconfigure(encoding="utf-8", errors="backslashreplace")


def _is_tty(stream: Any) -> bool:
    """Report whether ANSI colour is safe to emit on this stream."""
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _resolve_defaults(
    quiet_loggers: tuple[str, ...] | None,
    redact_keys: frozenset[str] | None,
) -> tuple[tuple[str, ...], frozenset[str]]:
    """Apply ``LOG_QUIET_LOGGERS`` / ``LOG_REDACT_KEYS`` env fallbacks.

    Extracted from ``configure_logging`` to keep that body near the 20-line
    limit in AGENTS.md §3.1.
    """
    if quiet_loggers is None:
        env_quiet = os.getenv("LOG_QUIET_LOGGERS")
        quiet_loggers = (
            tuple(name.strip() for name in env_quiet.split(",") if name.strip())
            if env_quiet
            else DEFAULT_QUIET_LOGGERS
        )
    if redact_keys is None:
        env_redact = os.getenv("LOG_REDACT_KEYS", "")
        redact_keys = DEFAULT_REDACT_KEYS | {
            key.strip().lower() for key in env_redact.split(",") if key.strip()
        }
    return quiet_loggers, redact_keys


def configure_logging(
    level: str | int | None = None,
    # `format` shadows the builtin. Kept deliberately: it is the existing public
    # API and is what every call site already passes by keyword.
    format: str | None = None,
    service_name: str | None = None,
    environment: str | None = None,
    *,
    redact_keys: frozenset[str] | None = None,
    quiet_loggers: tuple[str, ...] | None = None,
    handoff_loggers: tuple[str, ...] | None = None,
    include_source: bool = False,
    capture_warnings: bool = True,
    stream: Any | None = None,
) -> None:
    """Configure the root logger for the entire application.

    Call this exactly once at startup (for example, in the FastAPI lifespan).
    Every logger obtained via ``logging.getLogger(__name__)`` inherits it.

    Each argument falls back to an environment variable, so the same call works
    unchanged across projects: ``LOG_LEVEL``, ``LOG_FORMAT``, ``SERVICE_NAME``,
    ``ENVIRONMENT``, ``LOG_QUIET_LOGGERS`` (comma-separated), ``LOG_REDACT_KEYS``
    (comma-separated, *added* to the defaults rather than replacing them).

    Args:
        level: DEBUG | INFO | WARNING | ERROR | CRITICAL, or a numeric level.
        format: ``'json'`` (production) or ``'text'`` (development).
        service_name: Injected into every JSON log record.
        environment: Injected into every JSON log record.
        redact_keys: Field names to redact, replacing ``DEFAULT_REDACT_KEYS``.
            To extend instead: ``DEFAULT_REDACT_KEYS | {"prompt"}``.
        quiet_loggers: Third-party logger names to cap at WARNING.
        handoff_loggers: Loggers whose own handlers are removed so their records
            propagate to the root handler configured here.
        include_source: Emit file/line/function on every JSON record.
        capture_warnings: Route ``warnings.warn()`` through logging, so warnings
            land in the structured stream instead of raw stderr.
        stream: Target stream, defaulting to ``sys.stdout`` — containers expect
            logs on stdout. Injectable for tests.

    Example:
        >>> configure_logging(
        ...     level="INFO",
        ...     format="json",
        ...     service_name="llm-provider-service",
        ...     environment="production",
        ...     redact_keys=DEFAULT_REDACT_KEYS | {"prompt", "completion"},
        ... )
    """
    # `is None` rather than `or`: an explicitly passed empty string is a caller
    # decision, not a request to fall back to the environment.
    resolved_level = os.getenv("LOG_LEVEL", "INFO") if level is None else level
    resolved_format = os.getenv("LOG_FORMAT", "json") if format is None else format
    service = os.getenv("SERVICE_NAME", "app") if service_name is None else service_name
    env = os.getenv("ENVIRONMENT", "development") if environment is None else environment
    quiet_loggers, redact_keys = _resolve_defaults(quiet_loggers, redact_keys)

    target_stream = stream if stream is not None else sys.stdout
    _force_utf8(target_stream)

    formatter: logging.Formatter = (
        JSONFormatter(service_name=service, environment=env, include_source=include_source)
        if resolved_format == "json"
        else TextFormatter(use_colour=_is_tty(target_stream))
    )

    handler = logging.StreamHandler(target_stream)
    handler.setFormatter(formatter)
    # Order matters: inject ambient context first so those fields are subject to
    # redaction too. Reversing these two lines would let a secret carried in
    # log_context() reach the formatter untouched.
    handler.addFilter(_ContextInjectingFilter())
    handler.addFilter(RedactFilter(redact_keys))

    root_logger = logging.getLogger()
    # WHY clear: prevents duplicate output when configure_logging() runs after
    # something else (a test framework, basicConfig) already attached a handler.
    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
    root_logger.addHandler(handler)
    root_logger.setLevel(_resolve_level(resolved_level))

    for name in quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in handoff_loggers if handoff_loggers is not None else DEFAULT_HANDOFF_LOGGERS:
        handoff = logging.getLogger(name)
        handoff.handlers.clear()
        handoff.propagate = True

    logging.captureWarnings(capture_warnings)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger.

    Identical to ``logging.getLogger(name)``, kept so call sites import every
    logging concern from one place and can be redirected later without a
    project-wide find-and-replace.

    Args:
        name: Logger name, conventionally ``__name__``.

    Returns:
        The stdlib Logger for that name.
    """
    return logging.getLogger(name)
