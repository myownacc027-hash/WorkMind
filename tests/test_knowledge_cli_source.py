from pathlib import Path


def test_knowledge_cli_and_template_exist() -> None:
    assert Path("apps/knowledge_cli/main.py").exists()
    assert Path("apps/knowledge_cli/status.py").exists()
    assert Path("examples/knowledge_bundle.template.json").exists()
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "workmind-knowledge-import" in pyproject
    assert "workmind-knowledge-status" in pyproject
