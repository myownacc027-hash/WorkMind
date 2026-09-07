from pathlib import Path


def test_knowledge_service_contains_validation_primitives() -> None:
    source = Path("packages/knowledge/service.py").read_text(encoding="utf-8")
    assert "def normalize_text(" in source
    assert "def normalize_predicate(" in source
    assert "def parse_datetime(" in source
    assert "def import_knowledge_bundle(" in source
    assert "chunk_id" in source
    assert "does not belong to" in source
    assert '"apply": apply' in source
    assert "Full semantic dry-run passed and was rolled back" in source


def test_knowledge_service_is_parseable() -> None:
    import ast

    path = Path("packages/knowledge/service.py")
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
