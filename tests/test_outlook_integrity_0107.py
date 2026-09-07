from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator


SOURCE = Path(
    "apps/outlook_collector/outlook.py"
).read_text(encoding="utf-8")


class _Logger:
    def warning(self, *_args, **_kwargs):
        return None


namespace = {
    "Any": Any,
    "Callable": Callable,
    "Iterator": Iterator,
    "datetime": datetime,
    "UTC": UTC,
    "MAIL_ITEM_CLASS": 43,
    "logger": _Logger(),
    "_folder_path": lambda _folder: "Inbox",
}

tree = ast.parse(SOURCE)
selected = [
    node
    for node in tree.body
    if isinstance(
        node,
        (ast.FunctionDef, ast.AsyncFunctionDef),
    )
    and node.name
    in {
        "_to_utc",
        "_iter_recent_items",
        "_needs_attachment_refresh",
    }
]
module = ast.Module(body=selected, type_ignores=[])
exec(compile(module, "<outlook-audit>", "exec"), namespace)

_iter_recent_items = namespace["_iter_recent_items"]
_needs_attachment_refresh = namespace[
    "_needs_attachment_refresh"
]


class _Items:
    def __init__(self, values):
        self.values = values
        self.Count = len(values)

    def Sort(self, *_args):
        return None

    def Item(self, index):
        value = self.values[index - 1]
        if isinstance(value, Exception):
            raise value
        return value


class _Folder:
    def __init__(self, values):
        self.Items = _Items(values)


def _mail(hour: int):
    return SimpleNamespace(
        Class=43,
        ReceivedTime=datetime(
            2026,
            8,
            14,
            hour,
            0,
            tzinfo=UTC,
        ),
    )


def test_iterator_exposes_read_errors_to_caller() -> None:
    errors = []
    values = list(
        _iter_recent_items(
            _Folder(
                [
                    _mail(12),
                    RuntimeError("COM read failed"),
                    _mail(10),
                ]
            ),
            date_attr="ReceivedTime",
            since=datetime(
                2026,
                8,
                14,
                9,
                0,
                tzinfo=UTC,
            ),
            max_items=0,
            on_error=errors.append,
        )
    )

    assert len(values) == 2
    assert len(errors) == 1
    assert "COM read failed" in str(errors[0])


def test_outlook_save_error_is_retried() -> None:
    message = SimpleNamespace(
        has_attachments=True,
        attachments=[
            SimpleNamespace(
                archive_path=None,
                processing_status="error",
            )
        ],
    )
    assert _needs_attachment_refresh(
        message,
        save_enabled=True,
        created=False,
        content_changed=False,
    )


def test_extraction_error_does_not_refetch_saved_attachment(
) -> None:
    message = SimpleNamespace(
        has_attachments=True,
        attachments=[
            SimpleNamespace(
                archive_path="C:/archive/file.xlsx",
                processing_status="error",
            )
        ],
    )
    assert not _needs_attachment_refresh(
        message,
        save_enabled=True,
        created=False,
        content_changed=False,
    )


def test_items_item_access_is_inside_try_block() -> None:
    tree = ast.parse(SOURCE)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_iter_recent_items"
    )
    loop = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.For)
    )
    first_statement = loop.body[0]
    assert isinstance(first_statement, ast.Try)
    protected_source = ast.get_source_segment(
        SOURCE,
        first_statement,
    )
    assert "items.Item(index)" in protected_source
