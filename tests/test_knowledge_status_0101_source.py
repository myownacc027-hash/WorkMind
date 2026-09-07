from pathlib import Path


SERVICE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")


def test_status_separates_claims_relations_and_rows() -> None:
    status_start = SERVICE.index(
        "def knowledge_status("
    )
    status_source = SERVICE[status_start:]
    assert '"claims": {' in status_source
    assert '"relations": {' in status_source
    assert '"stored_claim_rows": {' in status_source
    assert "object_entity_id.is_(None)" in status_source
    assert "object_entity_id.is_not(None)" in status_source
