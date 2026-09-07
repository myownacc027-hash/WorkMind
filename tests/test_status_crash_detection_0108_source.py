from pathlib import Path
S=Path("apps/sync_runner/status.py").read_text(encoding="utf-8")

def test_status_uses_lock_to_detect_abandonment():
    assert "lock_is_held(" in S
    assert '"status"] = "abandoned"' in S
    assert '"abandoned": abandoned_reconciliation' in S

def test_runtime_helper_has_three_lock_states():
    assert "if operation_lock_held is True" in S
    assert "if operation_lock_held is False" in S
