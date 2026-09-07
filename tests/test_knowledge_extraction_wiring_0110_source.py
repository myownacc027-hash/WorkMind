from pathlib import Path


EMAIL_WORKER = Path(
    "apps/processing_worker/worker.py"
).read_text(encoding="utf-8")
ATTACHMENT_WORKER = Path(
    "apps/attachment_processor/worker.py"
).read_text(encoding="utf-8")
DOCUMENTS = Path(
    "packages/processing/documents.py"
).read_text(encoding="utf-8")
MCP = Path("apps/mcp_server/server.py").read_text(encoding="utf-8")
PYPROJECT = Path("pyproject.toml").read_text(encoding="utf-8")


def test_all_indexing_paths_enqueue_knowledge_candidates() -> None:
    assert 'source_type="email"' in EMAIL_WORKER
    assert 'reason="email_indexed"' in EMAIL_WORKER
    assert 'source_type="attachment"' in ATTACHMENT_WORKER
    assert 'reason="attachment_indexed"' in ATTACHMENT_WORKER
    assert 'source_type="document"' in DOCUMENTS
    assert 'reason="document_indexed"' in DOCUMENTS


def test_attachment_status_uses_per_source_chunk_count() -> None:
    assert "attachment_row_counts" in ATTACHMENT_WORKER
    assert "attachment_row_counts.get(attachment.id, 0)" in ATTACHMENT_WORKER


def test_mcp_exposes_queue_validation_apply_and_status_tools() -> None:
    for name in (
        "get_workmind_extraction_batch",
        "validate_workmind_extraction_job",
        "apply_workmind_extraction_job",
        "release_workmind_extraction_job",
        "get_workmind_extraction_status",
    ):
        assert name in MCP


def test_cli_entrypoints_exist() -> None:
    assert "workmind-knowledge-extraction =" in PYPROJECT
    assert "workmind-knowledge-extraction-status =" in PYPROJECT
    assert "workmind-knowledge-extract-now =" in PYPROJECT
