from pathlib import Path


WORKER = Path(
    "apps/processing_worker/worker.py"
).read_text(encoding="utf-8")
MAIN = Path(
    "apps/sync_runner/main.py"
).read_text(encoding="utf-8")
OUTLOOK = Path(
    "apps/outlook_collector/outlook.py"
).read_text(encoding="utf-8")


def test_mail_worker_reports_failed_jobs() -> None:
    assert '"failed": len(job_ids)' in WORKER
    assert '"failed": 0' in WORKER


def test_sync_surfaces_collection_and_index_errors() -> None:
    assert "collector_failed" in MAIN
    assert "worker_failed" in MAIN
    assert "attachment_worker_failed" in MAIN
    assert '"completed_with_errors"' in MAIN


def test_low_level_outlook_skips_are_counted() -> None:
    assert '"item_skipped": 0' in OUTLOOK
    assert 'counters["item_skipped"] += 1' in OUTLOOK
