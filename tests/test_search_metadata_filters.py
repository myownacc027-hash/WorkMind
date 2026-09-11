from __future__ import annotations

import uuid
from types import SimpleNamespace

import packages.search.hybrid as hybrid


def _candidate(
    *,
    source_id: uuid.UUID,
    rank: int,
    score: float,
    source_type: str = "email",
) -> dict:
    return {
        "rank": rank,
        "chunk_id": uuid.uuid4(),
        "source_type": source_type,
        "source_id": source_id,
        "chunk_index": 0,
        "text": "project alpha",
        "score": score,
    }


def _metadata(
    *,
    sender_name: str = "",
    sender_email: str = "",
    filename: str | None = None,
) -> dict:
    return {
        "source_ref": (
            "workmind://test/source"
        ),
        "parent_source_id": None,
        "parent_source_ref": None,
        "thread_id": None,
        "subject": "project alpha",
        "filename": filename,
        "mime_type": None,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "date": None,
        "date_value": None,
        "folder_path": None,
        "has_attachments": False,
        "content_hash": None,
    }


def test_metadata_filter_metric_counts_only_surviving_sources(
    monkeypatch,
) -> None:
    matching_id = uuid.uuid4()
    rejected_id = uuid.uuid4()

    candidates = [
        _candidate(
            source_id=matching_id,
            rank=1,
            score=1.0,
        ),
        _candidate(
            source_id=rejected_id,
            rank=2,
            score=0.8,
        ),
    ]

    monkeypatch.setattr(
        hybrid,
        "_metadata_eligible_source_ids",
        lambda **kwargs: {
            "email": (
                matching_id,
            ),
        },
    )

    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            candidates,
            [],
        ),
    )

    def fake_enrich(
        sources,
    ) -> None:
        for (
            _source_type,
            source_id,
        ), source in sources.items():

            if source_id == matching_id:
                source["metadata"] = (
                    _metadata(
                        sender_name=(
                            "Target Sender"
                        ),
                        sender_email=(
                            "target@example.test"
                        ),
                    )
                )

            else:
                source["metadata"] = (
                    _metadata(
                        sender_name=(
                            "Other Sender"
                        ),
                        sender_email=(
                            "other@example.test"
                        ),
                    )
                )

    monkeypatch.setattr(
        hybrid,
        "_enrich_sources",
        fake_enrich,
    )

    result = hybrid.search_hybrid(
        "project alpha",
        mode="lexical",
        source_type="email",
        sender="target@example.test",
        limit=10,
    )

    counts = result[
        "candidate_counts"
    ]

    assert (
        counts[
            "unique_sources_before_filters"
        ]
        == 2
    )

    assert (
        counts[
            "unique_sources_after_metadata_filters"
        ]
        == 1
    )

    assert (
        counts[
            "unique_sources_after_quality_gate"
        ]
        == 1
    )

    assert result["count"] == 1


def test_metadata_filter_metric_without_filter_keeps_all_sources(
    monkeypatch,
) -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()

    candidates = [
        _candidate(
            source_id=first_id,
            rank=1,
            score=1.0,
        ),
        _candidate(
            source_id=second_id,
            rank=2,
            score=0.8,
        ),
    ]

    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            candidates,
            [],
        ),
    )

    def fake_enrich(
        sources,
    ) -> None:
        for index, source in enumerate(
            sources.values(),
            start=1,
        ):
            source["metadata"] = (
                _metadata(
                    sender_name=(
                        f"Sender {index}"
                    ),
                    sender_email=(
                        f"sender{index}"
                        "@example.test"
                    ),
                )
            )

    monkeypatch.setattr(
        hybrid,
        "_enrich_sources",
        fake_enrich,
    )

    result = hybrid.search_hybrid(
        "project alpha",
        mode="lexical",
        source_type="email",
        limit=10,
    )

    counts = result[
        "candidate_counts"
    ]

    assert (
        counts[
            "unique_sources_before_filters"
        ]
        == 2
    )

    assert (
        counts[
            "unique_sources_after_metadata_filters"
        ]
        == 2
    )


def test_source_type_compatibility_for_metadata_filters() -> None:
    assert hybrid._eligible_source_types(
        "all",
        sender=None,
        filename=None,
    ) == (
        "email",
        "attachment",
        "document",
    )

    assert hybrid._eligible_source_types(
        "all",
        sender="sender",
        filename=None,
    ) == (
        "email",
        "attachment",
    )

    assert hybrid._eligible_source_types(
        "all",
        sender=None,
        filename="report.xlsx",
    ) == (
        "attachment",
        "document",
    )

    assert hybrid._eligible_source_types(
        "all",
        sender="sender",
        filename="report.xlsx",
    ) == (
        "attachment",
    )

    assert hybrid._eligible_source_types(
        "document",
        sender="sender",
        filename=None,
    ) == ()

    assert hybrid._eligible_source_types(
        "email",
        sender=None,
        filename="report.xlsx",
    ) == ()


def test_search_passes_eligible_ids_to_both_retrieval_channels(
    monkeypatch,
) -> None:
    eligible_id = uuid.uuid4()

    captured = {}

    monkeypatch.setattr(
        hybrid,
        "_metadata_eligible_source_ids",
        lambda **kwargs: {
            "email": (
                eligible_id,
            ),
        },
    )

    def fake_semantic(
        **kwargs,
    ):
        captured[
            "semantic"
        ] = kwargs[
            "eligible_source_ids"
        ]

        return [], []

    def fake_lexical(
        **kwargs,
    ):
        captured[
            "lexical"
        ] = kwargs[
            "eligible_source_ids"
        ]

        return [], []

    monkeypatch.setattr(
        hybrid,
        "_semantic_candidates",
        fake_semantic,
    )

    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        fake_lexical,
    )

    monkeypatch.setattr(
        hybrid,
        "_enrich_sources",
        lambda sources: None,
    )

    hybrid.search_hybrid(
        "project alpha",
        mode="hybrid",
        source_type="email",
        sender="target@example.test",
    )

    expected = (
        eligible_id,
    )

    assert (
        captured["semantic"]
        == expected
    )

    assert (
        captured["lexical"]
        == expected
    )


def test_semantic_pushdown_uses_source_id_match_any(
    monkeypatch,
) -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()

    captured = {}

    class FakeClient:
        def collection_exists(
            self,
            collection_name,
        ) -> bool:
            return True

        def get_collection(
            self,
            collection_name,
        ):
            return SimpleNamespace(
                payload_schema={
                    "source_type": object(),
                    "source_id": object(),
                }
            )

        def query_points(
            self,
            **kwargs,
        ):
            captured.update(
                kwargs
            )

            return SimpleNamespace(
                points=[],
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
            source_type="email",
            candidate_limit=20,
            min_score=0.0,
            eligible_source_ids=(
                first_id,
                second_id,
            ),
        )
    )

    assert rows == []
    assert warnings == []

    query_filter = captured[
        "query_filter"
    ]

    conditions = [
        condition
        for condition
        in query_filter.must
        if getattr(
            condition,
            "key",
            None,
        )
        == "source_id"
    ]

    assert len(conditions) == 1

    values = list(
        conditions[
            0
        ].match.any
    )

    assert values == [
        str(first_id),
        str(second_id),
    ]


def test_semantic_pushdown_refuses_unindexed_source_id(
    monkeypatch,
) -> None:
    eligible_id = uuid.uuid4()

    class FakeClient:
        def collection_exists(
            self,
            collection_name,
        ) -> bool:
            return True

        def get_collection(
            self,
            collection_name,
        ):
            return SimpleNamespace(
                payload_schema={
                    "source_type": object(),
                }
            )

    monkeypatch.setattr(
        hybrid,
        "get_qdrant_client",
        lambda: FakeClient(),
    )

    def must_not_embed(
        values,
    ):
        raise AssertionError(
            "embedding must not run "
            "without source_id index"
        )

    monkeypatch.setattr(
        hybrid,
        "embed_texts",
        must_not_embed,
    )

    rows, warnings = (
        hybrid._semantic_candidates(
            query="project alpha",
            source_type="email",
            candidate_limit=20,
            min_score=0.0,
            eligible_source_ids=(
                eligible_id,
            ),
        )
    )

    assert rows == []

    assert any(
        "source_id payload index"
        in warning
        for warning in warnings
    )


def test_empty_eligible_set_skips_semantic_before_embedding(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hybrid,
        "get_qdrant_client",
        lambda: (
            (_ for _ in ())
            .throw(
                AssertionError(
                    "Qdrant must not run"
                )
            )
        ),
    )

    monkeypatch.setattr(
        hybrid,
        "embed_texts",
        lambda values: (
            (_ for _ in ())
            .throw(
                AssertionError(
                    "embedding must not run"
                )
            )
        ),
    )

    rows, warnings = (
        hybrid._semantic_candidates(
            query="project alpha",
            source_type="email",
            candidate_limit=20,
            min_score=0.0,
            eligible_source_ids=(),
        )
    )

    assert rows == []
    assert warnings == []


def test_lexical_pushdown_places_source_ids_inside_sql_before_limit(
    monkeypatch,
) -> None:
    eligible_id = uuid.uuid4()
    captured = {}

    class FakeResult:
        def mappings(
            self,
        ):
            return self

        def all(
            self,
        ):
            return []

    class FakeSession:
        def __enter__(
            self,
        ):
            return self

        def __exit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return None

        def execute(
            self,
            statement,
            params,
        ):
            captured[
                "sql"
            ] = str(statement)

            captured[
                "params"
            ] = dict(params)

            return FakeResult()

    monkeypatch.setattr(
        hybrid,
        "_fts_index_available",
        lambda: True,
    )

    monkeypatch.setattr(
        hybrid,
        "SessionLocal",
        lambda: FakeSession(),
    )

    rows, warnings = (
        hybrid._lexical_candidates(
            query="project alpha",
            source_type="email",
            candidate_limit=250,
            eligible_source_ids=(
                eligible_id,
            ),
        )
    )

    assert rows == []
    assert warnings == []

    sql = captured[
        "sql"
    ]

    assert (
        "chunks.source_id"
        in sql
    )

    assert (
        "IN "
        in sql
    )

    assert (
        sql.find(
            "chunks.source_id"
        )
        < sql.find(
            "LIMIT"
        )
    )

    assert (
        captured[
            "params"
        ][
            "eligible_source_ids"
        ]
        == (
            eligible_id,
        )
    )


def test_lexical_document_rows_are_not_discarded(
    monkeypatch,
) -> None:
    document_id = uuid.uuid4()

    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            [
                _candidate(
                    source_id=(
                        document_id
                    ),
                    rank=1,
                    score=1.0,
                    source_type=(
                        "document"
                    ),
                ),
            ],
            [],
        ),
    )

    def fake_enrich(
        sources,
    ) -> None:
        source = sources[
            (
                "document",
                document_id,
            )
        ]

        source[
            "metadata"
        ] = _metadata(
            filename="report.txt",
        )

    monkeypatch.setattr(
        hybrid,
        "_enrich_sources",
        fake_enrich,
    )

    result = hybrid.search_hybrid(
        "project alpha",
        mode="lexical",
        source_type="document",
        limit=5,
    )

    assert result["count"] == 1

    assert (
        result["results"][0][
            "source_type"
        ]
        == "document"
    )
