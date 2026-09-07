from pathlib import Path


INSTALLER = Path(
    "scripts/install_knowledge_extraction_autostart.ps1"
).read_text(encoding="utf-8")
RUNNER = Path(
    "scripts/run_scheduled_knowledge_extraction.ps1"
).read_text(encoding="utf-8")


def test_extraction_scheduler_uses_ignore_new() -> None:
    assert "-MultipleInstances IgnoreNew" in INSTALLER
    assert 'WorkMind Knowledge Extraction' in INSTALLER


def test_extraction_runner_processes_bounded_batch() -> None:
    assert "workmind-knowledge-extract-now.exe" in RUNNER
    assert "--max-jobs 3" in RUNNER
