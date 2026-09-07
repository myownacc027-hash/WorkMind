from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _try_lock(handle) -> bool:
    try:
        handle.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                handle.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        handle.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                handle.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_UN,
            )
    except OSError:
        pass


def _ensure_lock_byte(handle) -> None:
    """
    Initialize byte 0 only after this process owns the lock.

    Windows can raise PermissionError when a process reads a byte
    currently protected by another process's byte-range lock.
    """
    handle.seek(0)

    if not handle.read(1):
        handle.seek(0)
        handle.write(b"0")
        handle.flush()


@contextmanager
def single_instance_lock(path: Path) -> Iterator[bool]:
    """
    Acquire a non-blocking cross-process lock.

    Lock contention is expected behavior. When another WorkMind
    process owns the lock, yield False instead of raising.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False

    try:
        # IMPORTANT: attempt the OS lock before touching byte 0.
        acquired = _try_lock(handle)

        if not acquired:
            yield False
            return

        _ensure_lock_byte(handle)
        yield True
    finally:
        if acquired:
            _unlock(handle)

        handle.close()



def lock_is_held(path: Path) -> bool | None:
    """Probe whether another process currently owns this lock."""
    try:
        with single_instance_lock(path) as acquired:
            return not acquired
    except OSError:
        return None
