from __future__ import annotations

import uuid

from packages.search import hybrid


def _row(
    *,
    text: str,
    rank: int = 1,
    chunk_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> dict:
    return {
        "rank": rank,
        "chunk_id": str(
            chunk_id
            or uuid.uuid4()
        ),
        "source_type": "email",
        "source_id": str(
            source_id
            or uuid.uuid4()
        ),
        "chunk_index": 0,
        "text": text,
        "score": 0.5,
    }


def test_rescue_policy_constants() -> None:
    assert (
        hybrid.LEXICAL_RESCUE_MAX_VARIANTS
        == 3
    )

    assert (
        hybrid.LEXICAL_RESCUE_ACCEPT_MAX_ROWS
        == 25
    )

    assert (
        hybrid.LEXICAL_RESCUE_PROBE_LIMIT
        == 26
    )

    assert (
        hybrid.LEXICAL_RESCUE_MIN_COVERAGE
        == 0.34
    )


def test_rescue_accepts_low_cardinality_candidate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_variants",
        lambda query: (
            "alpha beta",
        ),
    )

    expected = _row(
        text="alpha beta",
    )

    calls = []


    def fake_lexical(
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        return [
            dict(
                expected
            )
        ], []


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        fake_lexical,
    )


    rows, warnings = (
        hybrid._lexical_rescue_candidates(
            query=(
                "alpha beta gamma "
                "delta epsilon"
            ),
            source_type="all",
            candidate_limit=300,
            eligible_source_ids=None,
        )
    )


    assert warnings == []

    assert len(calls) == 1

    assert (
        calls[0][
            "candidate_limit"
        ]
        == 26
    )

    assert len(rows) == 1

    assert (
        rows[0][
            "chunk_id"
        ]
        == expected[
            "chunk_id"
        ]
    )

    assert rows[0]["rank"] == 1


def test_rescue_rejects_low_coverage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_variants",
        lambda query: (
            "alpha beta",
        ),
    )

    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            [
                _row(
                    text="alpha",
                )
            ],
            [],
        ),
    )


    rows, warnings = (
        hybrid._lexical_rescue_candidates(
            query=(
                "alpha beta gamma "
                "delta epsilon"
            ),
            source_type="all",
            candidate_limit=300,
        )
    )


    assert rows == []
    assert warnings == []


def test_rescue_rejects_broad_variant(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_variants",
        lambda query: (
            "alpha beta",
        ),
    )


    rows_26 = [
        _row(
            text=(
                "alpha beta gamma "
                "delta epsilon"
            ),
            rank=index,
        )
        for index in range(
            1,
            27,
        )
    ]


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            rows_26,
            [],
        ),
    )


    rows, warnings = (
        hybrid._lexical_rescue_candidates(
            query=(
                "alpha beta gamma "
                "delta epsilon"
            ),
            source_type="all",
            candidate_limit=300,
        )
    )


    assert rows == []
    assert warnings == []


def test_rescue_forwards_metadata_eligibility(
    monkeypatch,
) -> None:
    eligible_id = uuid.uuid4()

    captured = []


    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_variants",
        lambda query: (
            "alpha beta",
        ),
    )


    def fake_lexical(
        **kwargs,
    ):
        captured.append(
            kwargs[
                "eligible_source_ids"
            ]
        )

        return [], []


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        fake_lexical,
    )


    rows, warnings = (
        hybrid._lexical_rescue_candidates(
            query=(
                "alpha beta gamma"
            ),
            source_type="email",
            candidate_limit=300,
            eligible_source_ids=(
                eligible_id,
            ),
        )
    )


    assert rows == []
    assert warnings == []

    assert captured == [
        (
            eligible_id,
        )
    ]


def test_identifier_query_never_runs_rescue_probe(
    monkeypatch,
) -> None:
    calls = []


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            calls.append(
                kwargs
            )
            or [],
            [],
        ),
    )


    rows, warnings = (
        hybrid._lexical_rescue_candidates(
            query="189422",
            source_type="all",
            candidate_limit=300,
        )
    )


    assert rows == []
    assert warnings == []
    assert calls == []


def test_generated_identifier_variant_is_rejected(
    monkeypatch,
) -> None:
    calls = []


    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_variants",
        lambda query: (
            "189422",
            "alpha beta",
        ),
    )


    def fake_lexical(
        **kwargs,
    ):
        calls.append(
            kwargs[
                "query"
            ]
        )

        return [], []


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        fake_lexical,
    )


    hybrid._lexical_rescue_candidates(
        query=(
            "alpha beta gamma"
        ),
        source_type="all",
        candidate_limit=300,
    )


    assert calls == [
        "alpha beta"
    ]


def test_rescue_deduplicates_chunk_and_prefers_coverage(
    monkeypatch,
) -> None:
    shared_chunk = uuid.uuid4()
    shared_source = uuid.uuid4()


    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_variants",
        lambda query: (
            "alpha beta",
            "gamma delta",
        ),
    )


    def fake_lexical(
        **kwargs,
    ):
        if (
            kwargs["query"]
            == "alpha beta"
        ):
            return [
                _row(
                    text="alpha beta",
                    chunk_id=(
                        shared_chunk
                    ),
                    source_id=(
                        shared_source
                    ),
                )
            ], []

        return [
            _row(
                text=(
                    "alpha beta gamma "
                    "delta"
                ),
                chunk_id=(
                    shared_chunk
                ),
                source_id=(
                    shared_source
                ),
            )
        ], []


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        fake_lexical,
    )


    rows, warnings = (
        hybrid._lexical_rescue_candidates(
            query=(
                "alpha beta gamma "
                "delta epsilon"
            ),
            source_type="all",
            candidate_limit=300,
        )
    )


    assert warnings == []
    assert len(rows) == 1

    assert (
        rows[0]["text"]
        == (
            "alpha beta gamma "
            "delta"
        )
    )


def test_search_hybrid_invokes_rescue_only_after_empty_strict(
    monkeypatch,
) -> None:
    rescue_calls = []


    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            [],
            [],
        ),
    )


    def fake_rescue(
        **kwargs,
    ):
        rescue_calls.append(
            kwargs
        )

        return [], []


    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_candidates",
        fake_rescue,
    )


    result = hybrid.search_hybrid(
        "alpha beta gamma",
        mode="lexical",
        source_type="all",
        limit=10,
    )


    assert result["count"] == 0

    assert len(
        rescue_calls
    ) == 1

    assert (
        rescue_calls[0][
            "query"
        ]
        == "alpha beta gamma"
    )


def test_search_hybrid_does_not_rescue_failed_lexical_channel(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            [],
            [
                "lexical unavailable"
            ],
        ),
    )


    def unexpected_rescue(
        **kwargs,
    ):
        raise AssertionError(
            "rescue must not run "
            "after lexical warning"
        )


    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_candidates",
        unexpected_rescue,
    )


    result = hybrid.search_hybrid(
        "alpha beta gamma",
        mode="lexical",
        source_type="all",
        limit=10,
    )


    assert result["count"] == 0

    assert (
        "lexical unavailable"
        in result["warnings"]
    )


def test_search_hybrid_does_not_rescue_identifier(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        lambda **kwargs: (
            [],
            [],
        ),
    )


    def unexpected_rescue(
        **kwargs,
    ):
        raise AssertionError(
            "identifier query must not "
            "use natural-language rescue"
        )


    monkeypatch.setattr(
        hybrid,
        "_lexical_rescue_candidates",
        unexpected_rescue,
    )


    result = hybrid.search_hybrid(
        "189422",
        mode="lexical",
        source_type="all",
        limit=10,
    )


    assert result["count"] == 0
    assert (
        result[
            "identifier_query"
        ]
        is True
    )
