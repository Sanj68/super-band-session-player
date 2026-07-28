"""Cross-thread gate for durable session mutations and live Bridge overlays."""

from __future__ import annotations

from threading import Lock

_GATE = Lock()


def acquire() -> None:
    _GATE.acquire()


def try_acquire() -> bool:
    return _GATE.acquire(blocking=False)


def release() -> None:
    _GATE.release()
