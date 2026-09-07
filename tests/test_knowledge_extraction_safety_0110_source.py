from pathlib import Path


EXTRACTION = Path(
    "packages/knowledge/extraction.py"
).read_text(encoding="utf-8")
PROVIDER = Path(
    "packages/knowledge/openai_provider.py"
).read_text(encoding="utf-8")
CONFIG = Path(
    "packages/common/config.py"
).read_text(encoding="utf-8")


def test_core_indexing_uses_savepoint_safe_queue_wrapper() -> None:
    assert "def try_enqueue_indexed_source" in EXTRACTION
    assert "with session.begin_nested():" in EXTRACTION


def test_extraction_attempts_are_bounded() -> None:
    assert "KNOWLEDGE_EXTRACTION_MAX_ATTEMPTS" in CONFIG
    assert "knowledge_extraction_max_attempts" in EXTRACTION
    assert 'job.status = "error" if exhausted else "pending"' in EXTRACTION


def test_remote_provider_requires_explicit_data_consent() -> None:
    assert "KNOWLEDGE_EXTRACTION_REMOTE_CONSENT" in CONFIG
    assert "knowledge_extraction_remote_consent" in PROVIDER
    assert "Remote extraction is not authorized" in PROVIDER


def test_apply_rechecks_source_and_revalidates_evidence() -> None:
    assert "Source content changed after validation; requeue required." in EXTRACTION
    assert "Revalidate immediately before persistence" in EXTRACTION
    assert "Validated extraction became stale before apply" in EXTRACTION


def test_default_backfill_balances_requested_source_types() -> None:
    assert "per_type_limit" in EXTRACTION
    assert "all-types backfill being consumed" in EXTRACTION
    assert "entirely by email rows" in EXTRACTION
