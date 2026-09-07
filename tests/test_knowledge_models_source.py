from pathlib import Path


def test_knowledge_models_and_migration_exist() -> None:
    models = Path("packages/database/models.py").read_text(encoding="utf-8")
    migration = Path(
        "migrations/versions/0007_structured_knowledge.py"
    ).read_text(encoding="utf-8")
    for class_name in (
        "KnowledgeEntity",
        "KnowledgeClaim",
        "KnowledgeEvent",
        "KnowledgeEvidence",
    ):
        assert f"class {class_name}" in models
    assert 'revision: str = "0007_structured_knowledge"' in migration
    assert 'down_revision: str | None = "0006_external_documents"' in migration
    assert "ck_knowledge_claim_has_object" in models
    assert "ck_knowledge_evidence_one_parent" in models
