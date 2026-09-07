from pathlib import Path
S=Path("packages/common/locking.py").read_text(encoding="utf-8")

def test_lock_probe_is_nonblocking_wrapper():
    assert "def lock_is_held" in S
    assert "with single_instance_lock(path) as acquired" in S
    assert "return not acquired" in S
