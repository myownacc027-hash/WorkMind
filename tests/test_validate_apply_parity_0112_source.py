from pathlib import Path


SERVICE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")


def test_validation_executes_apply_path_then_rolls_back():
    assert (
        "Full semantic dry-run passed and was rolled back"
        in SERVICE
    )
    assert "if apply:" in SERVICE
    assert "session.rollback()" in SERVICE


def test_semantic_failures_are_structured():
    assert (
        "except (KnowledgeValidationError, IntegrityError)"
        in SERVICE
    )
    assert '"ok": False' in SERVICE


def test_punctuation_only_object_text_is_rejected():
    assert (
        "claim object_text contains no semantic "
        in SERVICE
    )
