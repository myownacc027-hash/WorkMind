from pathlib import Path


SERVICE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")
SERVER = Path(
    "apps/mcp_server/server.py"
).read_text(encoding="utf-8")


def test_relation_service_and_mcp_tool_exist() -> None:
    assert "def get_relations(" in SERVICE
    assert (
        '@audited_tool("get_workmind_relations")'
        in SERVER
    )
    assert "from_entity_ref" in SERVICE
    assert "to_entity_ref" in SERVICE


def test_entity_card_separates_relation_directions() -> None:
    assert 'result["outgoing_relations"]' in SERVICE
    assert 'result["incoming_relations"]' in SERVICE
    assert 'result["relations"]' in SERVICE


def test_relation_direction_validation() -> None:
    assert (
        "direction must be outgoing, incoming, or both"
        in SERVICE
    )
