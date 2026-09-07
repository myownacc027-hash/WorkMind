from __future__ import annotations

from pathlib import Path

from packages.common.locking import single_instance_lock


def test_single_instance_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "sync.lock"
    with single_instance_lock(lock_path) as first:
        assert first is True
        with single_instance_lock(lock_path) as second:
            assert second is False
