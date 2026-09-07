from pathlib import Path


def test_conversation_history_is_excluded() -> None:
    source = (
        Path("packages/common/config.py")
        .read_text(encoding="utf-8")
    )
    assert "Журнал бесед,Conversation History" in source


def test_purge_preserves_multi_location_messages() -> None:
    source = (
        Path("apps/history_importer/purge.py")
        .read_text(encoding="utf-8")
    )
    assert "messages_to_preserve" in source
    assert "remaining_location_by_message" in source
    assert "qdrant_points_deleted" in source
    assert "manifest_path" in source
