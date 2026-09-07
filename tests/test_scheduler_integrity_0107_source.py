from pathlib import Path


SOURCE = Path(
    "scripts/install_mail_sync_autostart.ps1"
).read_text(encoding="utf-8")


def test_task_execution_limit_is_two_hours() -> None:
    assert (
        "-ExecutionTimeLimit (New-TimeSpan -Hours 2)"
        in SOURCE
    )
