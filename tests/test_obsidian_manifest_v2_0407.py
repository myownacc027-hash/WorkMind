from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from packages.knowledge import (
    obsidian_export as export,
)


ENTITY_ID = (
    "11111111-1111-4111-8111-111111111111"
)

ENTITY_NAME = "Entity Alpha"

ENTITY_PATH = (
    "Entities/task/Entity Alpha.md"
)


def _sha256(
    value: str,
) -> str:
    return hashlib.sha256(
        value.encode(
            "utf-8"
        )
    ).hexdigest()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entity_markdown,
    navigation_markdown,
) -> None:
    summary = {
        "entity_id": ENTITY_ID,
        "entity_type": "task",
        "canonical_name": ENTITY_NAME,
        "status": "active",
    }

    raw = {
        "entity": dict(
            summary
        ),
        "claims": [],
        "outgoing_relations": [],
        "incoming_relations": [],
        "events": [],
        "dated_claims": [],
    }

    monkeypatch.setattr(
        export,
        "_list_entities",
        lambda **_kwargs: {
            "total_count": 1,
            "selected_count": 1,
            "entities": [
                dict(
                    summary
                )
            ],
        },
    )

    monkeypatch.setattr(
        export,
        "_load_entity_snapshot",
        lambda *_args, **_kwargs: dict(
            raw
        ),
    )

    monkeypatch.setattr(
        export,
        "_projection_counts",
        lambda *_args, **_kwargs: {
            "ordinary_claim_count": 0,
            "outgoing_relation_count": 0,
            "incoming_relation_count": 0,
            "event_count": 0,
            "dated_claim_count": 0,
        },
    )

    monkeypatch.setattr(
        export,
        "_assert_complete_service_snapshot",
        lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(
        export,
        "normalize_service_snapshot",
        lambda value: value,
    )

    monkeypatch.setattr(
        export,
        "render_entity_markdown",
        lambda _snapshot: (
            entity_markdown()
        ),
    )

    monkeypatch.setattr(
        export,
        "render_navigation_pages",
        lambda _entries: {
            "Home.md": (
                navigation_markdown()
            )
        },
    )


def test_manifest_v2_fingerprints_entire_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entity_text = "# Entity Alpha\n"
    navigation_text = "# Home\n"

    _install(
        monkeypatch,
        entity_markdown=lambda: entity_text,
        navigation_markdown=lambda: navigation_text,
    )

    manifest = (
        export.build_projection_manifest(
            limit=None,
            vault_root=tmp_path,
        )
    )

    entity_sha = _sha256(
        entity_text
    )

    navigation_sha = _sha256(
        navigation_text
    )

    expected_entity_fingerprint = (
        _sha256(
            (
                f"{ENTITY_PATH} "
                f"{entity_sha}"
            )
        )
    )

    expected_navigation_fingerprint = (
        _sha256(
            (
                "Home.md "
                f"{navigation_sha}"
            )
        )
    )

    expected_projection_fingerprint = (
        _sha256(
            (
                "entity "
                f"{ENTITY_PATH} "
                f"{entity_sha}\n"
                "navigation "
                "Home.md "
                f"{navigation_sha}"
            )
        )
    )

    assert (
        manifest["manifest_version"]
        == "wm-obsidian-manifest-v2"
    )

    assert (
        manifest["projection_version"]
        == "wm-obsidian-v1"
    )

    assert (
        manifest["entity_content_bytes"]
        == len(
            entity_text.encode(
                "utf-8"
            )
        )
    )

    assert (
        manifest["total_content_bytes"]
        == manifest[
            "entity_content_bytes"
        ]
    )

    assert (
        manifest[
            "navigation_content_bytes"
        ]
        == len(
            navigation_text.encode(
                "utf-8"
            )
        )
    )

    assert (
        manifest[
            "projected_content_bytes"
        ]
        == (
            manifest[
                "entity_content_bytes"
            ]
            + manifest[
                "navigation_content_bytes"
            ]
        )
    )

    assert (
        manifest[
            "entity_fingerprint"
        ]
        == expected_entity_fingerprint
    )

    assert (
        manifest[
            "navigation_fingerprint"
        ]
        == expected_navigation_fingerprint
    )

    assert (
        manifest[
            "projection_fingerprint"
        ]
        == expected_projection_fingerprint
    )

    assert (
        manifest[
            "manifest_fingerprint"
        ]
        == manifest[
            "projection_fingerprint"
        ]
    )


def test_navigation_change_changes_projection_not_entity_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = {
        "navigation": "# Home A\n",
    }

    _install(
        monkeypatch,
        entity_markdown=lambda: (
            "# Entity Alpha\n"
        ),
        navigation_markdown=lambda: (
            state["navigation"]
        ),
    )

    first = (
        export.build_projection_manifest(
            limit=None,
            vault_root=tmp_path,
        )
    )

    state[
        "navigation"
    ] = "# Home B\n"

    second = (
        export.build_projection_manifest(
            limit=None,
            vault_root=tmp_path,
        )
    )

    assert (
        first["entity_fingerprint"]
        == second["entity_fingerprint"]
    )

    assert (
        first["navigation_fingerprint"]
        != second["navigation_fingerprint"]
    )

    assert (
        first["projection_fingerprint"]
        != second["projection_fingerprint"]
    )

    assert (
        first["manifest_fingerprint"]
        != second["manifest_fingerprint"]
    )


def test_entity_change_changes_projection_not_navigation_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = {
        "entity": "# Entity A\n",
    }

    _install(
        monkeypatch,
        entity_markdown=lambda: (
            state["entity"]
        ),
        navigation_markdown=lambda: (
            "# Home\n"
        ),
    )

    first = (
        export.build_projection_manifest(
            limit=None,
            vault_root=tmp_path,
        )
    )

    state[
        "entity"
    ] = "# Entity B\n"

    second = (
        export.build_projection_manifest(
            limit=None,
            vault_root=tmp_path,
        )
    )

    assert (
        first["entity_fingerprint"]
        != second["entity_fingerprint"]
    )

    assert (
        first["navigation_fingerprint"]
        == second["navigation_fingerprint"]
    )

    assert (
        first["projection_fingerprint"]
        != second["projection_fingerprint"]
    )

    assert (
        second["manifest_fingerprint"]
        == second["projection_fingerprint"]
    )
