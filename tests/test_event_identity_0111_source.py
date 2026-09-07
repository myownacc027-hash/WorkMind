from pathlib import Path


SOURCE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")


def test_event_id_is_parsed_and_reused() -> None:
    assert '_uuid(item.get("event_id"), "event_id")' in SOURCE
    assert 'item["event_id"]' in SOURCE
    assert (
        "event_id refers to a different immutable "
        in SOURCE
    )


def test_event_id_must_exist() -> None:
    assert "event_id does not exist" in SOURCE
