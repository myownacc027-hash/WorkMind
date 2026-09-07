import ast
from pathlib import Path
from typing import Any


SOURCE = Path("packages/knowledge/extraction.py").read_text(encoding="utf-8")
tree = ast.parse(SOURCE)
selected = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {
        "_bundle_evidence_pairs",
        "_bundle_identity_metadata_evidence",
        "_check_evidence_scope",
    }
]
namespace: dict[str, Any] = {"Any": Any}
exec(
    compile(ast.Module(body=selected, type_ignores=[]), "<extraction-scope>", "exec"),
    namespace,
)
_bundle_evidence_pairs = namespace["_bundle_evidence_pairs"]
_check_evidence_scope = namespace["_check_evidence_scope"]


def test_bundle_evidence_pairs_collects_all_fact_sections() -> None:
    bundle = {
        "entities": [],
        "claims": [
            {
                "evidence": [
                    {"source_ref": "workmind://email/a", "chunk_id": "c1"}
                ]
            }
        ],
        "relations": [
            {
                "evidence": [
                    {"source_ref": "workmind://email/b", "chunk_id": "c2"}
                ]
            }
        ],
        "events": [
            {
                "evidence": [
                    {"source_ref": "workmind://document/d", "chunk_id": "c3"}
                ]
            }
        ],
    }
    assert _bundle_evidence_pairs(bundle) == [
        ("workmind://email/a", "c1"),
        ("workmind://email/b", "c2"),
        ("workmind://document/d", "c3"),
    ]


def test_evidence_outside_packet_is_rejected() -> None:
    packet = {
        "allowed_evidence": [
            {"source_ref": "workmind://email/a", "chunk_id": "c1"}
        ]
    }
    bundle = {
        "claims": [
            {
                "evidence": [
                    {"source_ref": "workmind://email/a", "chunk_id": "c2"}
                ]
            }
        ],
        "relations": [],
        "events": [],
    }
    errors = _check_evidence_scope(packet, bundle)
    assert len(errors) == 1
    assert "outside extraction packet" in errors[0]
