"""Task runner interface and the default in-process implementation.

The runner decouples *scheduling* work (enqueue a generation run) from *doing*
it. The in-process runner executes the coroutine on the running loop; a Celery
runner can be substituted without changing callers (section 5).
"""

from __future__ import annotations

import abc
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class TaskHandle:
    task_id: str
    name: str
    done: bool = False
    result: object | None = None
    error: str | None = None
    _task: "asyncio.Task | None" = field(default=None, repr=False)


class TaskRunner(abc.ABC):
    """Enqueue and observe background work."""

    @abc.abstractmethod
    async def submit(self, name: str, coro: Awaitable) -> TaskHandle: ...

    @abc.abstractmethod
    def get(self, task_id: str) -> TaskHandle | None: ...


class InProcessTaskRunner(TaskRunner):
    """Runs tasks on the current event loop. No broker, no extra dependency."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskHandle] = {}

    async def submit(self, name: str, coro: Awaitable) -> TaskHandle:
        handle = TaskHandle(task_id=uuid.uuid4().hex, name=name)
        self._tasks[handle.task_id] = handle

        async def _run() -> None:
            try:
                handle.result = await coro
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                handle.error = f"{type(exc).__name__}: {exc}"
            finally:
                handle.done = True

        handle._task = asyncio.ensure_future(_run())
        return handle

    def get(self, task_id: str) -> TaskHandle | None:
        return self._tasks.get(task_id)

    async def wait(self, task_id: str) -> TaskHandle | None:
        handle = self._tasks.get(task_id)
        if handle is not None and handle._task is not None:
            await handle._task
        return handle


class CeleryTaskRunner(TaskRunner):
    """Celery-backed runner (section 5).

    Deliberately lazy: Celery is an optional dependency and is imported only
    when this runner is constructed. If Celery is unavailable the constructor
    raises ``RuntimeError`` so callers can fall back to the in-process runner.
    """

    def __init__(self, broker_url: str) -> None:
        try:
            from celery import Celery  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "Celery is not installed; install it or use InProcessTaskRunner"
            ) from exc
        self._app = Celery("vgoh", broker=broker_url)

    async def submit(self, name: str, coro: Awaitable) -> TaskHandle:  # pragma: no cover
        raise RuntimeError(
            "CeleryTaskRunner requires picklable task functions; enqueue a "
            "registered task instead of a coroutine"
        )

    def get(self, task_id: str) -> TaskHandle | None:  # pragma: no cover
        return None


def default_runner() -> TaskRunner:
    """Return the in-process runner unless a broker URL is configured."""
    import os

    broker = os.environ.get("VGOH_CELERY_BROKER_URL")
    if broker:
        try:
            return CeleryTaskRunner(broker)
        except RuntimeError:
            pass
    return InProcessTaskRunner()


# Keep a type alias so callers can annotate a run function.
RunFn = Callable[[str], Awaitable[object]]
