import ast
from pathlib import Path


def test_external_document_migration_and_model_are_present() -> None:
    migration = Path(
        "migrations/versions/0006_external_documents.py"
    ).read_text(encoding="utf-8")
    models = Path("packages/database/models.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0006_external_documents"' in migration
    assert 'down_revision: str | None = "0005_agent_action_log"' in migration
    assert 'class ExternalDocument(Base):' in models
    assert '__tablename__ = "external_documents"' in models
    assert 'uq_external_documents_content_hash' in models


def test_document_source_is_supported_by_search_and_mcp() -> None:
    hybrid = Path("packages/search/hybrid.py").read_text(
        encoding="utf-8"
    )
    services = Path("apps/mcp_server/services.py").read_text(
        encoding="utf-8"
    )
    server = Path("apps/mcp_server/server.py").read_text(
        encoding="utf-8"
    )
    assert '"document"' in hybrid
    assert 'workmind://document/' in hybrid
    assert 'def get_external_document(' in services
    services_tree = ast.parse(
        services
    )

    assert any(
        isinstance(
            node,
            ast.Call,
        )
        and isinstance(
            node.func,
            ast.Attribute,
        )
        and node.func.attr
        == "startswith"
        and isinstance(
            node.func.value,
            ast.Name,
        )
        and node.func.value.id
        == "raw"
        and len(
            node.args
        )
        >= 1
        and isinstance(
            node.args[0],
            ast.Constant,
        )
        and node.args[0].value
        == "workmind://document/"
        for node in ast.walk(
            services_tree
        )
    )
    assert '@audited_tool("get_document_by_id")' in server


def test_document_commands_are_registered() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "workmind-documents-import" in project
    assert "workmind-documents-status" in project
    assert "workmind-documents-reprocess" in project
