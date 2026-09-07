from pathlib import Path


def test_entry_id_is_guarded() -> None:
    source = (
        Path("apps/history_importer/main.py")
        .read_text(encoding="utf-8")
    )
    assert "history_item_unsupported" in source
    assert '"skipped_unsupported": 0' in source
    assert "getattr(item, \"EntryID\", \"\")" in source


def test_postgresql_trim_fix_is_packaged() -> None:
    source = (
        Path("packages/database/repositories.py")
        .read_text(encoding="utf-8")
    )
    assert "func.both(" not in source
    assert "func.replace(" in source
