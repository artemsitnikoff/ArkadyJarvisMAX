"""Background-task helper that prevents Python's GC from collecting
fire-and-forget tasks before they complete.

`asyncio.create_task` only keeps a *weak* reference to the resulting Task.
If nothing else holds a strong reference, the GC can collect the task
mid-flight — symptom: callback never ack'd, button hangs in MAX.
See: https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task

Use `spawn(coro)` everywhere we'd otherwise write
`asyncio.create_task(coro)` and don't await the result.
"""

import asyncio
from typing import Any, Coroutine, Set

_BG_TASKS: Set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Schedule `coro` as a background task with a strong reference held
    in a module-level set until the task finishes. Returns the Task so
    callers can `cancel()` or `await` if they later need to."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task
