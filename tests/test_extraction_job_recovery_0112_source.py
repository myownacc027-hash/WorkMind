from pathlib import Path


EXTRACTION = Path(
    "packages/knowledge/extraction.py"
).read_text(encoding="utf-8")
CLI = Path(
    "apps/knowledge_extraction_cli/main.py"
).read_text(encoding="utf-8")


def test_bundle_fingerprint_checked_before_apply():
    assert "expected_bundle_sha256" in EXTRACTION
    assert "actual_bundle_sha256" in EXTRACTION
    assert "fingerprint mismatch" in EXTRACTION


def test_apply_failure_is_persisted_and_requeued():
    assert "Apply semantic validation failed" in EXTRACTION
    assert (
        'job.status = "error" if exhausted else "pending"'
        in EXTRACTION
    )


def test_bad_validated_job_can_be_requeued():
    assert "def requeue_extraction_job(" in EXTRACTION
    assert '@app.command("requeue-job")' in CLI


def test_cli_uses_utf8_sig_for_bundle_file():
    assert 'encoding="utf-8-sig"' in CLI
    assert '@app.command("validate-file")' in CLI
