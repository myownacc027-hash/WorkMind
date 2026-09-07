from pathlib import Path


SERVICE = Path("packages/knowledge/service.py").read_text(encoding="utf-8")


def test_conflicts_require_interval_overlap() -> None:
    start = SERVICE.index("def _mark_conflicts(")
    end = SERVICE.index("def _entity_counts(", start)
    block = SERVICE[start:end]
    assert "intervals_overlap(" in block
    assert 'claim.get("status") == "active"' in block
    assert '"conflict_intervals"' in block


def test_status_history_is_temporal() -> None:
    assert "def get_status_history(" in SERVICE
    assert "effective_at_as_of" in SERVICE
    assert "temporal_conflicts" in SERVICE
    assert '"[valid_from, valid_to)"' in SERVICE
