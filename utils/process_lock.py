from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path


_local_locks: dict[str, threading.RLock] = {}
_local_locks_guard = threading.Lock()
_thread_state = threading.local()


class ProcessLockTimeout(TimeoutError):
    pass


def _lock_name(identity: str | Path) -> str:
    digest = hashlib.sha256(str(identity).casefold().encode("utf-8")).hexdigest()[:24]
    return f"Local\\MiChecklist_{digest}"


def _lock_path(identity: str | Path) -> Path:
    path = Path(identity)
    return path.with_name(path.name + ".lock")


@contextmanager
def process_lock(identity: str | Path, timeout_seconds: float = 30.0):
    """Provide a re-entrant lock shared by the desktop GUI and Windows service."""
    name = _lock_name(identity)
    with _local_locks_guard:
        lock = _local_locks.setdefault(name, threading.RLock())
    if not lock.acquire(timeout=max(0.0, timeout_seconds)):
        raise ProcessLockTimeout(f"Timed out waiting for lock: {identity}")
    if os.name != "nt":
        try:
            yield
        finally:
            lock.release()
        return

    import msvcrt

    depths = getattr(_thread_state, "depths", {})
    _thread_state.depths = depths
    if depths.get(name, 0):
        depths[name] += 1
        try:
            yield
        finally:
            depths[name] -= 1
            lock.release()
        return

    lock_path = _lock_path(identity)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ProcessLockTimeout(f"Timed out waiting for lock: {identity}") from exc
                time.sleep(0.05)
        depths[name] = 1
        try:
            yield
        finally:
            depths.pop(name, None)
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        stream.close()
        lock.release()

