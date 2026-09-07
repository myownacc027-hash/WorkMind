from pathlib import Path


def test_context_bundle_exposes_evidence_candidates() -> None:
    services = Path("apps/mcp_server/services.py").read_text(encoding="utf-8")
    server = Path("apps/mcp_server/server.py").read_text(encoding="utf-8")
    assert "def _evidence_candidates(" in services
    assert '"evidence_candidates"' in services
    assert '"classification_required"' in services
    assert "query: str | None = None" in server
