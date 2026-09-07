from pathlib import Path


SOURCE = Path(
    "apps/history_importer/main.py"
).read_text(encoding="utf-8")


def test_failed_item_is_not_written_to_history_cursor() -> None:
    marker = '"history_message_failed"'
    marker_at = SOURCE.index(marker)
    start = SOURCE.rindex(
        "except Exception as exc:",
        0,
        marker_at,
    )
    end = SOURCE.index(
        'if totals["seen"] % 100 == 0:',
        marker_at,
    )
    block = SOURCE[start:end]

    assert "state.cursor_entry_id = entry_id" not in block
    assert "state.cursor_timestamp = occurred_at" not in block
    assert "break" in block


def test_failed_history_run_is_paused() -> None:
    assert 'or totals["failed"] > 0' in SOURCE
    assert 'totals["folders_completed"]' in SOURCE
