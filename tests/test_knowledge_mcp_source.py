from pathlib import Path


def test_knowledge_mcp_tools_and_audit_redaction() -> None:
    server = Path("apps/mcp_server/server.py").read_text(encoding="utf-8")
    audit = Path("apps/mcp_server/audit.py").read_text(encoding="utf-8")
    for tool_name in (
        "extract_workmind_knowledge",
        "search_workmind_entities",
        "get_workmind_entity",
        "get_workmind_claims",
        "get_workmind_timeline",
        "get_workmind_knowledge_status",
    ):
        assert f'@audited_tool("{tool_name}")' in server
    assert 'key == "bundle_json"' in audit
    assert '"sha256": hashlib.sha256' in audit
