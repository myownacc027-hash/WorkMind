from pathlib import Path


def test_packet_exposes_bounded_source_metadata_evidence() -> None:
    source = Path("packages/knowledge/extraction.py").read_text(encoding="utf-8")
    assert '"allowed_source_metadata_evidence": source_metadata_evidence' in source
    assert "def _allowed_source_metadata_evidence(" in source
    assert '"content_hash": attachment.content_hash' in source
    assert '"size_bytes": attachment.size_bytes' in source
    assert '[item["text"] for item in chunks] + metadata_identity_texts' in source


def test_metadata_scope_is_checked_separately_from_chunk_scope() -> None:
    source = Path("packages/knowledge/extraction.py").read_text(encoding="utf-8")
    assert "def _bundle_identity_metadata_evidence(" in source
    assert 'packet.get("allowed_source_metadata_evidence", [])' in source
    assert "Source metadata evidence outside extraction packet" in source
    assert "Source metadata evidence does not match packet value" in source


def test_functional_evidence_contract_remains_chunk_scoped() -> None:
    source = Path("packages/knowledge/extraction.py").read_text(encoding="utf-8")
    assert "Every claim, relation, and event must cite at least one" in source
    assert "Source metadata proves" in source
    service = Path("packages/knowledge/service.py").read_text(encoding="utf-8")
    assert "def _evidence_items(" in service
    assert "_validated_evidence(session, item, default_role)" in service
    assert "def _identity_evidence_items(" in service
    assert "_validated_metadata_evidence(session, item)" in service
