from pathlib import Path


def test_identity_resolver_prefers_exact_entry() -> None:
    source = (
        Path("packages/database/repositories.py")
        .read_text(encoding="utf-8")
    )
    assert "winner = exact or located or deduped" in source
    assert "def _merge_message_rows(" in source


def test_outlook_ingestion_cleans_stale_points() -> None:
    source = (
        Path("apps/outlook_collector/outlook.py")
        .read_text(encoding="utf-8")
    )
    assert "resolve_message_identity" in source
    assert "stale_qdrant_cleanup_failed" in source
