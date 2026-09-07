from datetime import UTC, datetime, timedelta
from pathlib import Path
from apps.sync_runner.reconciliation import mark_reconciliation_completed, mark_reconciliation_started, recover_abandoned_reconciliation, reconciliation_health

def _success(t):
    return {"status":"completed","mode":"reconciliation","started_at":(t-timedelta(minutes=2)).isoformat(),"finished_at":t.isoformat(),"duration_seconds":120,"lookback_days":14,"max_per_folder":0,"collector":{"seen":499,"created":0,"updated":4,"failed":0}}

def test_abandoned_recovery_preserves_success(tmp_path: Path):
    state=tmp_path/"state.json"; success=datetime(2026,8,14,8,24,tzinfo=UTC); started=success+timedelta(minutes=2); recovered=started+timedelta(minutes=3)
    mark_reconciliation_completed(state,result=_success(success))
    mark_reconciliation_started(state,started_at=started,mode="reconciliation",lookback_days=14,max_per_folder=0)
    value=recover_abandoned_reconciliation(state,recovered_at=recovered,reason="forced kill")
    assert value["last_status"]=="aborted" and value["last_run"]["status"]=="aborted" and value["abandoned_count"]==1
    health=reconciliation_health(state_file=state,sync_status_file=tmp_path/"s.json",sync_log_file=tmp_path/"l.jsonl",enabled=True,required_days=14,interval_hours=24,stale_hours=36,now=recovered)
    assert health["last_success_at"]==success.isoformat() and health["due"] is False and health["stale"] is False

def test_recovery_idempotent(tmp_path: Path):
    state=tmp_path/"state.json"; t=datetime(2026,8,14,8,26,tzinfo=UTC)
    mark_reconciliation_started(state,started_at=t,mode="reconciliation",lookback_days=14,max_per_folder=0)
    first=recover_abandoned_reconciliation(state,recovered_at=t+timedelta(minutes=1),reason="forced kill")
    second=recover_abandoned_reconciliation(state,recovered_at=t+timedelta(minutes=2),reason="again")
    assert first["abandoned_count"]==1 and second["abandoned_count"]==1 and second["last_error"]=="forced kill"
