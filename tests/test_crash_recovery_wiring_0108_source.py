from pathlib import Path
M=Path("apps/sync_runner/main.py").read_text(encoding="utf-8")
R=Path("apps/sync_runner/reconciliation.py").read_text(encoding="utf-8")

def test_runner_repairs_orphan_after_lock():
    i=M.index("with single_instance_lock(")
    j=M.index("recover_abandoned_reconciliation(", i)
    k=M.index("mark_reconciliation_started(", j)
    assert i < j < k

def test_recovery_does_not_overwrite_last_success():
    b=R[R.index("def recover_abandoned_reconciliation"):]
    assert '"last_status": "aborted"' in b
    assert '"last_success_at"' not in b
    assert '"last_success_run"' not in b
