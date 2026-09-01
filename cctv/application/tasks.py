"""Lifecycle-aware supervision for CCTV background work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


class TaskSupervisor:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger(__name__)
        self._tasks: set[asyncio.Task[object]] = set()
        self._closing = False

    def open(self) -> None:
        if self._tasks:
            raise RuntimeError("Cannot reopen while tasks are active.")
        self._closing = False

    def create(
        self, coroutine: Coroutine[Any, Any, object], *, name: str
    ) -> asyncio.Task[object] | None:
        if self._closing:
            coroutine.close()
            return None
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._log.error(
                "cctv: background task %s failed",
                task.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    async def shutdown(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


__all__ = ["TaskSupervisor"]
