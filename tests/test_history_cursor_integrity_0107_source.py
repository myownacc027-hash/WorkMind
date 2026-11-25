import ast
from pathlib import Path


SOURCE = Path(
    "apps/history_importer/main.py"
).read_text(encoding="utf-8")


def test_failed_item_is_not_written_to_history_cursor() -> None:
    tree = ast.parse(SOURCE)
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and any(
            isinstance(child, ast.Constant)
            and child.value == "history_message_failed"
            for child in ast.walk(node)
        )
    ]
    assert len(handlers) == 1
    block = handlers[0]
    nodes = list(ast.walk(block))

    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and node.attr in {"cursor_entry_id", "cursor_timestamp", "metadata_json"}
        for node in nodes
    )
    assert any(isinstance(node, ast.Break) for node in nodes)
    calls = {
        ast.unparse(node.func)
        for node in nodes
        if isinstance(node, ast.Call)
    }
    assert {"session.rollback", "session.commit"} <= calls
    assert "_record_history_cursor" not in calls
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "reached_folder_end"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
        for node in nodes
    )


def test_failed_history_run_is_paused() -> None:
    assert 'or totals["failed"] > 0' in SOURCE
    assert 'totals["folders_completed"]' in SOURCE
