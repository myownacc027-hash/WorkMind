from __future__ import annotations

import multiprocessing
from pathlib import Path

from packages.common.locking import single_instance_lock


def _hold_lock(
    path: str,
    ready: multiprocessing.Event,
    release: multiprocessing.Event,
) -> None:
    with single_instance_lock(Path(path)) as acquired:
        if not acquired:
            raise RuntimeError(
                "first process did not acquire lock"
            )

        ready.set()
        release.wait(timeout=10)


def test_second_process_returns_false_without_exception(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "workmind.lock"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()

    process = multiprocessing.Process(
        target=_hold_lock,
        args=(str(lock_path), ready, release),
    )
    process.start()

    try:
        assert ready.wait(timeout=5)

        with single_instance_lock(lock_path) as acquired:
            assert acquired is False
    finally:
        release.set()
        process.join(timeout=5)

        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0


def test_lock_can_be_acquired_after_release(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "workmind.lock"

    with single_instance_lock(lock_path) as acquired:
        assert acquired is True

    with single_instance_lock(lock_path) as acquired:
        assert acquired is True


def test_lock_file_is_initialized(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "workmind.lock"

    with single_instance_lock(lock_path) as acquired:
        assert acquired is True

    assert lock_path.read_bytes()[:1] == b"0"
