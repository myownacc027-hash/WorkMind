from pathlib import Path


def test_agent_action_model_and_migration_exist() -> None:
    models = Path("packages/database/models.py").read_text(encoding="utf-8")
    migration = Path(
        "migrations/versions/0005_agent_action_log.py"
    ).read_text(encoding="utf-8")
    assert "class AgentActionLog" in models
    assert 'revision: str = "0005_agent_action_log"' in migration
    assert 'down_revision: str | None = "0004_hybrid_search_fts"' in migration


def test_every_mcp_tool_is_audited() -> None:
    source = Path("apps/mcp_server/server.py").read_text(encoding="utf-8")
    assert source.count("@mcp.tool()") == source.count("@audited_tool(")
    assert "Separate direct statements" in source
