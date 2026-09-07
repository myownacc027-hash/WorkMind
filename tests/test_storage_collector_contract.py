from __future__ import annotations

import ast
from pathlib import Path


SOURCE_PATH = Path(
    "apps/outlook_collector/outlook.py"
)


def _function(
    tree: ast.Module,
    name: str,
) -> ast.FunctionDef:
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == name
        ):
            return node

    raise AssertionError(
        f"Function not found: {name}"
    )


def test_save_attachment_accepts_storage_context() -> None:
    source = SOURCE_PATH.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    function = _function(
        tree,
        "_save_attachment",
    )

    arguments = {
        item.arg
        for item in (
            list(function.args.args)
            + list(function.args.kwonlyargs)
        )
    }

    assert "session" in arguments
    assert "settings" in arguments


def test_ingest_passes_storage_context_to_save_attachment() -> None:
    source = SOURCE_PATH.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    function = _function(
        tree,
        "ingest_mail_item",
    )

    calls = []

    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue

        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "_save_attachment"
        ):
            calls.append(node)

    assert len(calls) == 1

    keyword_names = {
        item.arg
        for item in calls[0].keywords
        if item.arg is not None
    }

    assert "index" in keyword_names
    assert "session" in keyword_names
    assert "settings" in keyword_names


def test_ingest_links_attachment_to_stored_object() -> None:
    source = SOURCE_PATH.read_text(
        encoding="utf-8"
    )

    start = source.index(
        "def ingest_mail_item("
    )
    end = source.index(
        "\ndef collect_mail(",
        start,
    )

    body = source[start:end]

    assert (
        "stored_object_id=stored_object.id"
        in body
    )

    assert (
        '"content_addressed_storage": True'
        in body
    )

def test_save_attachment_return_is_unpacked_as_filename_and_stored_object() -> None:
    source = SOURCE_PATH.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    function = _function(
        tree,
        "ingest_mail_item",
    )

    matching_assignments = []

    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue

        value = node.value

        if not isinstance(value, ast.Call):
            continue

        if not (
            isinstance(value.func, ast.Name)
            and value.func.id == "_save_attachment"
        ):
            continue

        matching_assignments.append(node)

    assert len(matching_assignments) == 1

    assignment = matching_assignments[0]

    assert len(assignment.targets) == 1

    target = assignment.targets[0]

    assert isinstance(target, ast.Tuple)

    names = [
        item.id
        for item in target.elts
        if isinstance(item, ast.Name)
    ]

    assert names == [
        "filename",
        "stored_object",
    ]

