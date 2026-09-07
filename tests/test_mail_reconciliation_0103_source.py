from pathlib import Path


MAIN = Path(
    "apps/sync_runner/main.py"
).read_text(encoding="utf-8")
STATUS = Path(
    "apps/sync_runner/status.py"
).read_text(encoding="utf-8")
SCRIPT = Path(
    "scripts/run_scheduled_mail_sync.ps1"
).read_text(encoding="utf-8")
CONFIG = Path(
    "packages/common/config.py"
).read_text(encoding="utf-8")


def test_scheduled_runner_enables_daily_reconciliation() -> None:
    assert "--reconcile-days 14" in SCRIPT
    assert "--reconcile-interval-hours 24" in SCRIPT
    assert "--reconcile-max-per-folder 0" in SCRIPT


def test_sync_runner_has_reconciliation_mode() -> None:
    assert '"reconciliation"' in MAIN
    assert "--force-reconciliation" in MAIN
    assert "run_reconciliation" in MAIN
    assert "incremental=(" in MAIN


def test_status_exposes_health_and_warning() -> None:
    assert '"attention_required"' in STATUS
    assert '"warnings"' in STATUS
    assert "reconciliation is stale" in STATUS


def test_reconciliation_configuration_and_state_path() -> None:
    assert "MAIL_RECONCILIATION_ENABLED" in CONFIG
    assert "MAIL_RECONCILIATION_DAYS" in CONFIG
    assert "MAIL_RECONCILIATION_INTERVAL_HOURS" in CONFIG
    assert "mail-reconciliation-status.json" in CONFIG
