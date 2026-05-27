"""STORY-727: Shared async helper for the improvement pipeline.

The improvement modules (pattern_detector, proposal_generator, approval_handler,
tracker, retrospective) have a mix of async DB calls and sync orchestration code
(cron driver). Each module previously carried its own copy of `_run_async`.
This module is the single source of truth so the asyncio fallback semantics
(running loop → schedule, idle loop → run_until_complete, closed loop → new
loop) are defined once.
"""
from __future__ import annotations

import asyncio
from typing import Any


def run_async(coro: Any, *, timeout: float = 10.0) -> Any:
    """Run a coroutine synchronously.

    - If a loop is running in this thread, schedule on it via
      run_coroutine_threadsafe and wait up to ``timeout`` seconds.
    - Otherwise create a new loop and drive it with run_until_complete.
    """
    try:
        loop = asyncio.get_running_loop()
        # A loop is running — schedule the coroutine on it
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    except RuntimeError:
        # No running loop — safe to drive synchronously
        pass

    # No running loop: use asyncio.run() which creates and cleans up a new loop.
    # This is the recommended approach in Python 3.10+ and avoids the
    # DeprecationWarning from asyncio.get_event_loop() when no loop exists.
    return asyncio.run(coro)
