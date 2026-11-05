"""Fire-and-forget tasks that can't be garbage-collected mid-run.

asyncio only keeps a weak reference to a task, so a bare `asyncio.create_task(...)` whose result is thrown away may
be collected before it finishes. spawn() keeps a strong reference until the task is done and logs it if it crashes
(otherwise the error only shows up as "Task exception was never retrieved", if at all).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("latochan")
_live: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _live.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task: asyncio.Task) -> None:
    _live.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error("background task %s crashed", task.get_name(), exc_info=task.exception())
