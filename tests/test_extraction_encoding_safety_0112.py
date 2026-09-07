import ast
from pathlib import Path
from typing import Any


SOURCE = Path(
    "packages/knowledge/extraction.py"
).read_text(encoding="utf-8")

tree = ast.parse(SOURCE)
wanted = {
    "_canonical_bundle_json",
    "_bundle_sha256",
    "_encoding_loss_errors",
}
nodes = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in wanted
]
namespace = {
    "Any": Any,
    "hashlib": __import__("hashlib"),
    "json": __import__("json"),
    "_DURABLE_TEXT_FIELDS": {
        "canonical_name",
        "description",
        "object_text",
        "claim_text",
        "title",
        "excerpt",
    },
}
exec(
    compile(
        ast.Module(body=nodes, type_ignores=[]),
        "<encoding-safety>",
        "exec",
    ),
    namespace,
)


def test_question_mark_encoding_loss_is_detected():
    errors = namespace["_encoding_loss_errors"](
        {
            "claims": [
                {
                    "object_text":
                        "?????????? ?? ?????????",
                }
            ]
        }
    )
    assert errors
    assert "encoding loss" in errors[0]


def test_normal_russian_text_is_not_rejected():
    errors = namespace["_encoding_loss_errors"](
        {
            "claims": [
                {
                    "object_text":
                        "Возвращена на доработку",
                }
            ]
        }
    )
    assert errors == []


def test_bundle_hash_is_order_independent():
    hash_fn = namespace["_bundle_sha256"]
    assert hash_fn(
        {"b": 2, "a": 1}
    ) == hash_fn(
        {"a": 1, "b": 2}
    )
