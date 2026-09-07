from pathlib import Path


def test_current_version_and_migration_head() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.12.1"' in pyproject
    migration = Path(
        "migrations/versions/0010_source_metadata_evidence.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "0010_source_metadata_evidence"' in migration
    assert 'down_revision: str | None = "0009_knowledge_extraction_queue"' in migration
