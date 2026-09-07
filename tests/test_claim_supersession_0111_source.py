from pathlib import Path


SOURCE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")


def test_supersession_closes_previous_claim() -> None:
    assert 'previous.status = "superseded"' in SOURCE
    assert "previous.valid_to = boundary" in SOURCE
    assert "claim.valid_from" in SOURCE
    assert "claim.observed_at" in SOURCE


def test_supersession_requires_same_subject_predicate() -> None:
    assert (
        "supersedes_claim_id must refer to the same "
        in SOURCE
    )
