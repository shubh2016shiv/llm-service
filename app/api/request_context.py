"""Request correlation and final exception safety at the ASGI boundary.

Pure ASGI middleware avoids the task-switching wrapper used by decorator
middleware and behaves correctly for long-lived streaming responses. Caller
request IDs are validated before they can reach logs or response headers.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from app.core.logging import log_context

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class _RequestBodyTooLarge(Exception):
    """Internal control-flow signal raised while consuming a chunked body."""


def resolve_request_id(incoming_request_id: str | None) -> tuple[str, bool]:
    """Return a safe request ID and whether a supplied value was accepted."""
    if incoming_request_id and _REQUEST_ID_PATTERN.fullmatch(incoming_request_id):
        return incoming_request_id, True
    return str(uuid.uuid4()), incoming_request_id is None


class RequestContextMiddleware:
    """Validate, bind, and echo one correlation ID for every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("X-Request-ID")
        request_id, accepted = resolve_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id
        if incoming is not None and not accepted:
            logger.warning("Rejected invalid X-Request-ID header")

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        with log_context(request_id=request_id):
            logger.debug(
                "Request received",
                extra={"method": scope.get("method"), "path": scope.get("path")},
            )
            await self.app(scope, receive, send_with_request_id)


class RequestBodyLimitMiddleware:
    """Bound memory exposure before FastAPI parses an inbound request body.

    ``Content-Length`` permits an immediate rejection. Chunked requests have no
    declared size, so the receive wrapper counts bytes as they arrive and stops
    forwarding once the same limit is crossed.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be at least 1.")
        self.app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await self._error_response(scope, receive, send, status_code=400)
                return
            if declared_size < 0:
                await self._error_response(scope, receive, send, status_code=400)
                return
            if declared_size > self._max_body_bytes:
                await self._error_response(scope, receive, send, status_code=413)
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def track_response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, track_response)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await self._error_response(scope, receive, send, status_code=413)

    async def _error_response(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
    ) -> None:
        """Return a stable boundary error without exposing request content."""
        invalid_length = status_code == 400
        response = JSONResponse(
            status_code=status_code,
            content={
                "detail": (
                    "Content-Length must be a non-negative integer."
                    if invalid_length
                    else f"Request body exceeds the {self._max_body_bytes}-byte limit."
                ),
                "error_code": (
                    "INVALID_CONTENT_LENGTH" if invalid_length else "REQUEST_BODY_TOO_LARGE"
                ),
                "request_id": scope.get("state", {}).get("request_id"),
            },
        )
        await response(scope, receive, send)


class UnhandledExceptionMiddleware:
    """Return a sanitized 500 while preserving CORS and correlation headers.

    If a stream already started, HTTP cannot replace it with JSON; re-raising
    closes the connection rather than mixing incompatible wire formats.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def track_response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        except Exception:
            logger.exception(
                "Unhandled exception reached API safety net",
                extra={"method": scope.get("method"), "path": scope.get("path")},
            )
            if response_started:
                raise
            request_id = scope.get("state", {}).get("request_id")
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "An unexpected error occurred.",
                    "error_code": "INTERNAL_SERVER_ERROR",
                    "request_id": request_id,
                },
            )
            await response(scope, receive, send)
