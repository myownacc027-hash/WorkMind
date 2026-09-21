from __future__ import annotations

import uuid
from types import SimpleNamespace

from qdrant_client import models as qmodels

from packages.common.config import Settings
from packages.search import hybrid


def test_hybrid_search_hnsw_ef_default() -> None:
    field = Settings.model_fields[
        "hybrid_search_hnsw_ef"
    ]

    assert field.default == 2048


def test_semantic_candidates_use_configured_hnsw_ef(
    monkeypatch,
) -> None:
    captured = {}

    source_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    point_id = uuid.uuid4()

    class FakeClient:
        def collection_exists(
            self,
            collection_name,
        ):
            captured[
                "collection_exists"
            ] = collection_name

            return True

        def query_points(
            self,
            **kwargs,
        ):
            captured.update(
                kwargs
            )

            point = SimpleNamespace(
                id=point_id,
                score=0.91,
                payload={
                    "source_type": "email",
                    "source_id": str(
                        source_id
                    ),
                    "chunk_id": str(
                        chunk_id
                    ),
                    "chunk_index": 0,
                    "text": "project alpha",
                },
            )

            return SimpleNamespace(
                points=[
                    point
                ]
            )

    settings = SimpleNamespace(
        qdrant_collection=(
            "workmind-test"
        ),
        hybrid_search_hnsw_ef=2048,
    )

    monkeypatch.setattr(
        hybrid,
        "get_settings",
        lambda: settings,
    )

    monkeypatch.setattr(
        hybrid,
        "get_qdrant_client",
        lambda: FakeClient(),
    )

    monkeypatch.setattr(
        hybrid,
        "embed_texts",
        lambda values: [
            [
                0.1,
                0.2,
                0.3,
            ]
        ],
    )

    rows, warnings = (
        hybrid._semantic_candidates(
            query="project alpha",
            source_type="all",
            candidate_limit=25,
            min_score=0.0,
            eligible_source_ids=None,
        )
    )

    assert warnings == []
    assert len(rows) == 1

    assert (
        captured[
            "collection_name"
        ]
        == "workmind-test"
    )

    assert (
        captured[
            "limit"
        ]
        == 25
    )

    assert (
        captured[
            "score_threshold"
        ]
        is None
    )

    assert (
        captured[
            "query_filter"
        ]
        is None
    )

    search_params = captured[
        "search_params"
    ]

    assert isinstance(
        search_params,
        qmodels.SearchParams,
    )

    assert (
        search_params.hnsw_ef
        == 2048
    )

    assert (
        search_params.exact
        is False
    )


def test_semantic_candidates_take_hnsw_ef_from_settings(
    monkeypatch,
) -> None:
    captured = {}

    class FakeClient:
        def collection_exists(
            self,
            collection_name,
        ):
            return True

        def query_points(
            self,
            **kwargs,
        ):
            captured.update(
                kwargs
            )

            return SimpleNamespace(
                points=[]
            )

    settings = SimpleNamespace(
        qdrant_collection=(
            "workmind-test"
        ),
        hybrid_search_hnsw_ef=512,
    )

    monkeypatch.setattr(
        hybrid,
        "get_settings",
        lambda: settings,
    )

    monkeypatch.setattr(
        hybrid,
        "get_qdrant_client",
        lambda: FakeClient(),
    )

    monkeypatch.setattr(
        hybrid,
        "embed_texts",
        lambda values: [
            [
                0.1,
                0.2,
            ]
        ],
    )

    rows, warnings = (
        hybrid._semantic_candidates(
            query="project alpha",
            source_type="all",
            candidate_limit=10,
            min_score=0.0,
            eligible_source_ids=None,
        )
    )

    assert rows == []
    assert warnings == []

    assert (
        captured[
            "search_params"
        ].hnsw_ef
        == 512
    )
