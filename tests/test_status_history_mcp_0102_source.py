from pathlib import Path


SERVER = Path("apps/mcp_server/server.py").read_text(encoding="utf-8")


def test_status_history_mcp_tool_registered() -> None:
    assert '@audited_tool("get_workmind_status_history")' in SERVER
    assert "get_structured_status_history" in SERVER
    assert "Temporal intervals use [valid_from, valid_to)" in SERVER
