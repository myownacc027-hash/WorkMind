from pathlib import Path


def test_folder_wrappers_are_reopened_by_id() -> None:
    source = (
        Path("apps/history_importer/main.py")
        .read_text(encoding="utf-8")
    )
    assert "namespace.GetFolderFromID(" in source
    assert "def _resilient_history_items(" in source
    assert "history_folder_connection_lost" in source
    assert "outlook_temporarily_unavailable" in source


def test_folder_spec_does_not_require_live_wrapper() -> None:
    source = (
        Path("apps/history_importer/main.py")
        .read_text(encoding="utf-8")
    )
    assert "folder: Any | None = None" in source
    assert '"folder_reconnects": 0' in source
