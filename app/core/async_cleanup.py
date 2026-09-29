"""Wait for owned cleanup without forwarding caller cancellation.

Architecture:
    Inference services / streaming response -> wait_for_cleanup -> owned task

The caller retains cancellation semantics, but quota and capacity cleanup finish
first. Cleanup operations must supply their own timeout so this wait is bounded.
"""

from __future__ import annotations

import asyncio


async def wait_for_cleanup(task: asyncio.Task[None]) -> None:
    """Finish an owned task despite repeated cancellation of its waiter."""
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception:
            break  # Retrieve the failure below, after restoring cancellation.
    if cancellation is not None:
        if not task.cancelled():
            task.exception()  # Observe failures even when cancellation wins.
        raise cancellation
    task.result()
