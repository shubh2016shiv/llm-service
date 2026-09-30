"""Verify that the load probe measures the documented SSE wire contract.

Architecture:
    Mock HTTP transport -> streaming load probe -> normalized SSE event outcomes
"""

from __future__ import annotations

import httpx
import pytest

from smoke_test.streaming_load import _run_one


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_ok"),
    [
        (
            'event: text_delta\ndata: {"data":{"content":"hello"}}\n\n'
            'event: complete\ndata: {"data":{"status":"completed"}}\n\n',
            True,
        ),
        (
            'event: text_delta\ndata: {"data":{"content":"hello"}}\n\n'
            'event: error\ndata: {"data":{"code":"UPSTREAM_ERROR"}}\n\n'
            'event: complete\ndata: {"data":{"status":"failed"}}\n\n',
            False,
        ),
        ('event: complete\ndata: {"data":{"status":"completed"}}\n\n', False),
        ("data: [DONE]\n\n", False),
    ],
)
async def test_load_probe_requires_real_content_and_successful_terminal_event(
    body: str,
    expected_ok: bool,
) -> None:
    """REQ: completion means a content token and successful named terminal event."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
        base_url="http://test",
    ) as client:
        result = await _run_one(
            client,
            tenant_id="test-tenant",
            deployment_key="test-route",
            token="test-token",
        )

    assert result.ok is expected_ok
    assert (result.first_token_ms is not None) is ("hello" in body)
