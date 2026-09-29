"""Tie eagerly acquired stream resources to the complete HTTP response lifetime.

Architecture:
    inference router -> ManagedStreamingResponse -> SSE iterator + owned source

Closing an unstarted async generator does not run its finally block. The
response therefore also owns the source directly, including when header delivery
fails before the first body read. Source closure must be bounded and idempotent.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from starlette.responses import StreamingResponse

from app.core.async_cleanup import wait_for_cleanup
from app.streaming.sse_delivery import AsyncClosable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from starlette.types import Receive, Scope, Send


class ManagedStreamingResponse(StreamingResponse):
    """Close both delivery and its owned source on every ASGI exit path."""

    def __init__(
        self,
        content: AsyncIterator[str],
        *,
        source: AsyncClosable,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Retain the eagerly acquired source independently of body iteration."""
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self._source = source

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Release resources after normal completion, disconnect, or send failure."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            await wait_for_cleanup(asyncio.create_task(self._close_owned_stream()))

    async def _close_owned_stream(self) -> None:
        """Stop any pending delivery read before closing the source itself."""
        try:
            if isinstance(self.body_iterator, AsyncClosable):
                await self.body_iterator.aclose()
        finally:
            await self._source.aclose()
