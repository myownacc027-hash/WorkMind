from pathlib import Path


def test_email_worker_claims_only_email_jobs() -> None:
    source = (
        Path("apps/processing_worker/worker.py")
        .read_text(encoding="utf-8")
    )
    assert 'ProcessingJob.job_type == "index_email"' in source
    assert 'ProcessingJob.source_type == "email"' in source


def test_attachment_worker_payload_links_parent_email() -> None:
    source = (
        Path("apps/attachment_processor/worker.py")
        .read_text(encoding="utf-8")
    )
    assert '"parent_source_ref"' in source
    assert '"source_type": "attachment"' in source
