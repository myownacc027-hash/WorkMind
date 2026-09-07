from pathlib import Path


SERVICE = Path(
    "packages/knowledge/service.py"
).read_text(encoding="utf-8")
SERVER = Path(
    "apps/mcp_server/server.py"
).read_text(encoding="utf-8")


def test_excerpt_is_opt_in() -> None:
    assert "include_excerpt: bool = False" in SERVICE
    assert "include_excerpt: bool = False" in SERVER
    assert 'if include_excerpt:' in SERVICE
    assert 'item["excerpt"] = row.excerpt' in SERVICE
