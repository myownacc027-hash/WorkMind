from pathlib import Path


MODELS = Path("packages/database/models.py").read_text(encoding="utf-8")
MIGRATION = Path(
    "migrations/versions/0009_knowledge_extraction_queue.py"
).read_text(encoding="utf-8")


def test_extraction_job_model_has_unique_source_version_identity() -> None:
    assert "class KnowledgeExtractionJob" in MODELS
    assert "uq_knowledge_extraction_source_version" in MODELS
    assert "lease_token" in MODELS
    assert "validation_json" in MODELS
    assert "apply_json" in MODELS


def test_migration_creates_queue_indexes_and_lease_columns() -> None:
    assert '"knowledge_extraction_jobs"' in MIGRATION
    assert "ix_knowledge_extraction_status_available" in MIGRATION
    assert "ix_knowledge_extraction_lease" in MIGRATION
    assert 'sa.Column("lease_token"' in MIGRATION
