from pathlib import Path


def test_document_scheduler_scripts_exist() -> None:
    expected = (
        "scripts/install_document_import_autostart.ps1",
        "scripts/uninstall_document_import_autostart.ps1",
        "scripts/run_scheduled_document_import.ps1",
        "scripts/run_scheduled_document_import_hidden.vbs",
    )
    for value in expected:
        assert Path(value).exists(), value


def test_document_scheduler_uses_two_overlap_guards() -> None:
    installer = Path(
        "scripts/install_document_import_autostart.ps1"
    ).read_text(encoding="utf-8")
    importer = Path(
        "apps/document_ingestion/main.py"
    ).read_text(encoding="utf-8")
    assert "-MultipleInstances IgnoreNew" in installer
    assert "single_instance_lock(" in importer
    assert '"status": "skipped_locked"' in importer


def test_document_status_exposes_health_and_stuck_rows() -> None:
    source = Path(
        "apps/document_ingestion/status.py"
    ).read_text(encoding="utf-8")
    assert '"attention_required": attention_required' in source
    assert '"stuck_processing_documents": stuck_count' in source
    assert '"recent_errors": [' in source
    assert '"last_run_looks_stale": run_looks_stale' in source


def test_reprocess_supports_stuck_documents() -> None:
    source = Path(
        "apps/document_ingestion/reprocess.py"
    ).read_text(encoding="utf-8")
    assert 'stuck: bool = typer.Option(False, "--stuck")' in source
    assert '"--stuck-minutes"' in source
    assert 'ExternalDocument.processing_status == "processing"' in source


def test_importer_logs_running_and_final_status() -> None:
    source = Path(
        "apps/document_ingestion/main.py"
    ).read_text(encoding="utf-8")
    assert '"status": "running"' in source
    assert '"completed_with_errors"' in source
    assert '"status": "failed"' in source
    assert "document_import_log_file" in source
