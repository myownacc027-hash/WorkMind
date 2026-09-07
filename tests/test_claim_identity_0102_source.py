from pathlib import Path


MODELS = Path("packages/database/models.py").read_text(encoding="utf-8")
SERVICE = Path("packages/knowledge/service.py").read_text(encoding="utf-8")
MIGRATION = Path(
    "migrations/versions/0008_temporal_claim_identity.py"
).read_text(encoding="utf-8")


def test_claim_identity_columns_and_migration() -> None:
    assert "claim_key: Mapped[str]" in MODELS
    assert "supersedes_claim_id" in MODELS
    assert "superseded_by_claim_id" in MODELS
    assert 'revision: str = "0008_temporal_claim_identity"' in MIGRATION
    assert 'down_revision: str | None = "0007_structured_knowledge"' in MIGRATION
    assert "SET claim_key = claim_hash" in MIGRATION


def test_import_supports_stable_update_identity() -> None:
    assert 'item.get("claim_id")' in SERVICE
    assert 'item.get("claim_key")' in SERVICE
    assert 'identity_source = "claim_id"' in SERVICE
    assert 'identity_source = "claim_key"' in SERVICE
    assert 'claim.valid_from = item["valid_from"]' in SERVICE
    assert 'claim.valid_to = item["valid_to"]' in SERVICE
