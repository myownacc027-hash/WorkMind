from pathlib import Path


SERVICE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")


def test_timeline_separates_events_and_dated_claims() -> None:
    assert '"events": events' in SERVICE
    assert '"dated_claims": dated_claims' in SERVICE
    assert '"timeline_type": "dated_claim"' in SERVICE
    assert "not necessarily the " in SERVICE
    assert "occurrence time of a real-world event" in SERVICE


def test_dated_claims_exclude_relations() -> None:
    timeline_start = SERVICE.index("def get_timeline(")
    timeline_end = SERVICE.index(
        "def get_entity(",
        timeline_start,
    )
    timeline_source = SERVICE[
        timeline_start:timeline_end
    ]
    assert (
        "KnowledgeClaim.object_entity_id.is_(None)"
        in timeline_source
    )
