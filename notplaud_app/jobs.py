"""Background processing queue.

Local Whisper and llama.cpp runs take minutes. Everything heavy happens on a
worker thread so the UI stays responsive and can poll for status.
"""

from __future__ import annotations

import copy
import queue
import threading
import traceback
from typing import Any, Callable

# (note_id, detail_level_or_None)
_QUEUE: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
_WORKER: threading.Thread | None = None
_LOCK = threading.Lock()
_ACTIVE: set[str] = set()

# Injected by app.py to avoid a circular import.
_RUNNER: Callable[[str, str | None], None] | None = None


def configure(runner: Callable[[str, str | None], None]) -> None:
    global _RUNNER
    _RUNNER = runner


def start_worker() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_loop, name="notplaud-worker", daemon=True)
        _WORKER.start()


def enqueue(note_id: str, detail_level: str | None = None) -> None:
    with _LOCK:
        _ACTIVE.add(note_id)
    _QUEUE.put((note_id, detail_level))
    start_worker()


def pending_ids() -> list[str]:
    with _LOCK:
        return sorted(_ACTIVE)


def pending_count() -> int:
    with _LOCK:
        return len(_ACTIVE)


def is_pending(note_id: str) -> bool:
    with _LOCK:
        return note_id in _ACTIVE


def _loop() -> None:
    while True:
        note_id, detail = _QUEUE.get()
        try:
            if _RUNNER is not None:
                _RUNNER(note_id, detail)
        except Exception:
            traceback.print_exc()
        finally:
            with _LOCK:
                _ACTIVE.discard(note_id)
            _QUEUE.task_done()


def snapshot(value: Any) -> Any:
    """Deep copy so worker threads never share mutable state with the server."""
    return copy.deepcopy(value)
