from pathlib import Path


SERVICE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")
EXTRACTION = Path(
    "packages/knowledge/extraction.py"
).read_text(encoding="utf-8")


def test_entity_plan_accepts_exact_entity_id():
    assert '"entity_id": entity_id' in SERVICE
    assert "entity_id does not exist" in SERVICE
    assert (
        "entity_id and external_id refer to different entities"
        in SERVICE
    )


def test_agent_is_told_to_copy_candidate_entity_id():
    assert (
        "copy that exact entity_id into the matching "
        in EXTRACTION
    )
