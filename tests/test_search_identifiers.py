from __future__ import annotations

import re

import pytest

import packages.search.hybrid as hybrid
from packages.search.ranking import (
    compound_identifier_matches,
    is_identifier_query,
    parse_compound_identifier,
    rank_signals,
)


ST_199_VARIANTS = (
    "СТ-199",
    "СТ 199",
    "СТ_199",
    "СТ–199",
    "СТ—199",
    "СТ №199",
    "СТ № 199",
)

IVP_912_VARIANTS = (
    "ИВП 912",
    "ИВП-912",
    "ИВП №912",
)


@pytest.mark.parametrize(
    "value",
    ST_199_VARIANTS,
)
def test_st_199_variants_have_same_canonical_identifier(
    value: str,
) -> None:
    parsed = parse_compound_identifier(value)

    assert parsed is not None
    assert parsed.prefix == "ст"
    assert parsed.number == "199"
    assert parsed.canonical == "ст-199"


@pytest.mark.parametrize(
    "value",
    IVP_912_VARIANTS,
)
def test_ivp_912_variants_have_same_canonical_identifier(
    value: str,
) -> None:
    parsed = parse_compound_identifier(value)

    assert parsed is not None
    assert parsed.prefix == "ивп"
    assert parsed.number == "912"
    assert parsed.canonical == "ивп-912"


@pytest.mark.parametrize(
    "value",
    (
        *ST_199_VARIANTS,
        *IVP_912_VARIANTS,
        "195930",
        "#195930",
    ),
)
def test_identifier_classifier_accepts_supported_identifier_forms(
    value: str,
) -> None:
    assert is_identifier_query(value)


@pytest.mark.parametrize(
    "value",
    (
        "отчет 2026",
        "версия 12",
        "письмо 199",
        "задача на 199 страниц",
    ),
)
def test_identifier_classifier_does_not_capture_normal_phrases(
    value: str,
) -> None:
    assert not is_identifier_query(value)


@pytest.mark.parametrize(
    "query",
    ST_199_VARIANTS,
)
@pytest.mark.parametrize(
    "text",
    (
        "Требования СТ-199 действуют",
        "Требования СТ 199 действуют",
        "Требования СТ_199 действуют",
        "Требования СТ–199 действуют",
        "Требования СТ—199 действуют",
        "Требования СТ №199 действуют",
        "Требования СТ № 199 действуют",
    ),
)
def test_structural_match_accepts_equivalent_st_199_forms(
    query: str,
    text: str,
) -> None:
    assert compound_identifier_matches(
        query,
        text,
    )


@pytest.mark.parametrize(
    "query",
    IVP_912_VARIANTS,
)
@pytest.mark.parametrize(
    "text",
    (
        "Используется ИВП 912",
        "Используется ИВП-912",
        "Используется ИВП №912",
        "Используется ИВП № 912",
    ),
)
def test_structural_match_accepts_equivalent_ivp_912_forms(
    query: str,
    text: str,
) -> None:
    assert compound_identifier_matches(
        query,
        text,
    )


@pytest.mark.parametrize(
    "text",
    (
        (
            "СТ находится здесь, а после большого количества "
            "другого текста встречается 199"
        ),
        (
            "СТ относится к документу. "
            "Номер другого документа 199."
        ),
        (
            "ИВП применяется в системе, "
            "всего обработано 912 документов."
        ),
        "СТ 198",
        "СТ 1990",
        "XСТ-199",
        "СТ-199X",
    ),
)
def test_structural_match_rejects_unrelated_or_partial_values(
    text: str,
) -> None:
    assert not compound_identifier_matches(
        "СТ-199",
        text,
    )


def test_rank_signals_treat_equivalent_identifier_as_exact_match() -> None:
    signals = rank_signals(
        query="СТ-199",
        content="Применяется стандарт СТ №199.",
        metadata_text="",
        semantic_score=None,
        lexical_score=1.0,
        semantic_rank=None,
        lexical_rank=1,
        mode="lexical",
    )

    assert signals.exact == 1.0
    assert signals.coverage == 1.0
    assert signals.primary_anchor == 1.0
    assert "identifier_equivalent" in signals.reasons


def test_rank_signals_reject_disconnected_identifier_tokens() -> None:
    signals = rank_signals(
        query="СТ-199",
        content=(
            "СТ относится к одному документу. "
            "После описания другого процесса встречается номер 199."
        ),
        metadata_text="",
        semantic_score=None,
        lexical_score=1.0,
        semantic_rank=None,
        lexical_rank=1,
        mode="lexical",
    )

    assert signals.exact == 0.0
    assert signals.coverage == 0.0
    assert signals.primary_anchor == 0.0


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("semantic", (True, False)),
        ("lexical", (False, True)),
        ("hybrid", (True, True)),
    ),
)
def test_channel_plan_for_natural_language_queries(
    mode: str,
    expected: tuple[bool, bool],
) -> None:
    assert (
        hybrid._channel_plan(
            mode,
            identifier_query=False,
        )
        == expected
    )


@pytest.mark.parametrize(
    "mode",
    (
        "semantic",
        "lexical",
        "hybrid",
    ),
)
def test_identifier_queries_bypass_semantic_channel(
    mode: str,
) -> None:
    assert hybrid._channel_plan(
        mode,
        identifier_query=True,
    ) == (False, True)


def test_compound_identifier_sql_prefilter_uses_prefix_plus_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        def mappings(self) -> "FakeResult":
            return self

        def all(self) -> list[object]:
            return []

    class FakeSession:
        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(
            self,
            exc_type,
            exc,
            traceback,
        ) -> None:
            return None

        def execute(
            self,
            statement,
            params,
        ) -> FakeResult:
            captured["sql"] = str(statement)
            captured["params"] = dict(params)
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

    rows, warnings = hybrid._lexical_candidates(
        query="СТ-199",
        source_type="all",
        candidate_limit=250,
    )

    assert rows == []
    assert warnings == []

    sql = str(captured["sql"])
    params = captured["params"]

    assert "@@ search_query.prefix_query" in sql
    assert "~* :identifier_pattern" in sql
    assert ":identifier_terms" not in sql

    assert params["identifier_prefix"] == "ст"

    pattern = str(
        params["identifier_pattern"]
    )

    positives = (
        "СТ-199",
        "СТ 199",
        "СТ_199",
        "СТ–199",
        "СТ—199",
        "СТ №199",
        "СТ № 199",
    )

    negatives = (
        "СТ находится здесь 199",
        "СТ 198",
        "СТ 1990",
        "XСТ-199",
        "СТ-199X",
    )

    for value in positives:
        assert re.search(
            pattern,
            value,
            flags=re.IGNORECASE,
        )

    for value in negatives:
        assert not re.search(
            pattern,
            value,
            flags=re.IGNORECASE,
        )


def test_numeric_identifier_routing_is_preserved() -> None:
    for query in (
        "195930",
        "#195930",
    ):
        assert is_identifier_query(query)

        assert hybrid._channel_plan(
            "semantic",
            identifier_query=True,
        ) == (False, True)