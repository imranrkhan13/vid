"""Background worker: a loop over durable state. No in-memory queue is authoritative, so killing the
process at any point loses nothing: state is reloaded from the database on the next start."""
from __future__ import annotations
import asyncio
import threading

from app.core.config import settings
from app.workflows import engine

_thread: threading.Thread | None = None
_stop = threading.Event()


def _run():
    engine.recover()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while not _stop.is_set():
        try:
            loop.run_until_complete(engine.tick())
        except Exception as e:  # noqa: BLE001
            print("worker tick error:", repr(e))
        _stop.wait(settings.tick_seconds)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="vgoh-worker", daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=10)
