from __future__ import annotations

import pytest

from packages.search import hybrid
from packages.search.ranking import (
    is_identifier_query,
)


FREE_FORM_CASES = (
    (
        "Коллеги, направляю информацию. "
        "Ответ направить user@example.org. "
        "Дата 17.09.2026."
    ),
    (
        "Направлено user@example.org "
        "по вопросу 123."
    ),
    (
        "Коллеги, заявка №123 рассматривается. "
        "Ответственный user@example.org. "
        "Подробное описание вопроса приведено ниже "
        "и относится к обычному тексту сообщения, "
        "а не к поиску одного идентификатора."
    ),
)


@pytest.mark.parametrize(
    "value",
    FREE_FORM_CASES,
)
def test_free_form_source_text_is_not_identifier(
    value: str,
) -> None:
    assert (
        is_identifier_query(value)
        is False
    )


@pytest.mark.parametrize(
    "value",
    (
        "Б24 №189422",
        "ИВП-912",
        "СТ_199",
        "010460406000123421ABCD",
        "2544",
        "#195930",
        "user123@example.org",
    ),
)
def test_existing_compact_identifiers_are_preserved(
    value: str,
) -> None:
    assert (
        is_identifier_query(value)
        is True
    )


def test_existing_identifier_channel_contract_is_preserved() -> None:

    for mode in (
        "semantic",
        "lexical",
        "hybrid",
    ):
        assert hybrid._channel_plan(
            mode,
            identifier_query=True,
        ) == (
            False,
            True,
        )


def test_free_form_semantic_source_text_does_not_dispatch_lexical(
    monkeypatch,
) -> None:

    query = FREE_FORM_CASES[0]

    assert (
        is_identifier_query(query)
        is False
    )

    calls = {
        "semantic": 0,
        "lexical": 0,
    }

    monkeypatch.setattr(
        hybrid,
        "_metadata_eligible_source_ids",
        lambda **_kwargs: None,
    )

    monkeypatch.setattr(
        hybrid,
        "_flatten_eligible_source_ids",
        lambda _value: None,
    )

    def fake_semantic(
        **_kwargs,
    ):
        calls["semantic"] += 1

        return (
            [],
            [],
        )

    def forbidden_lexical(
        **_kwargs,
    ):
        calls["lexical"] += 1

        raise AssertionError(
            "free-form semantic source text "
            "dispatched lexical retrieval"
        )

    monkeypatch.setattr(
        hybrid,
        "_semantic_candidates",
        fake_semantic,
    )

    monkeypatch.setattr(
        hybrid,
        "_lexical_candidates",
        forbidden_lexical,
    )

    monkeypatch.setattr(
        hybrid,
        "_enrich_sources",
        lambda _sources: None,
    )

    result = hybrid.search_hybrid(
        query,
        mode="semantic",
        limit=1,
    )

    assert (
        result["identifier_query"]
        is False
    )

    assert calls == {
        "semantic": 1,
        "lexical": 0,
    }


class _FakeResult:

    def mappings(
        self,
    ):
        return self

    def all(
        self,
    ):
        return []


class _FakeSession:

    def __init__(
        self,
        calls,
        *,
        fail_fts: bool = False,
    ):
        self.calls = calls
        self.fail_fts = fail_fts

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
        params=None,
    ):
        sql = str(
            statement
        )

        parameters = dict(
            params or {}
        )

        self.calls.append(
            (
                sql,
                parameters,
            )
        )

        if (
            self.fail_fts
            and "set_config" not in sql
        ):
            raise RuntimeError(
                "canceling statement due to "
                "statement timeout"
            )

        return _FakeResult()


@pytest.mark.parametrize(
    "query",
    (
        "project alpha",
        "СТ-199",
    ),
)
def test_lexical_queries_apply_local_timeout(
    monkeypatch,
    query: str,
) -> None:

    calls = []

    monkeypatch.setattr(
        hybrid,
        "_fts_index_available",
        lambda: True,
    )

    monkeypatch.setattr(
        hybrid,
        "SessionLocal",
        lambda: _FakeSession(
            calls
        ),
    )

    rows, warnings = (
        hybrid._lexical_candidates(
            query=query,
            source_type="all",
            candidate_limit=50,
        )
    )

    assert rows == []

    assert warnings == []

    assert len(calls) >= 2

    assert (
        "set_config"
        in calls[0][0]
    )

    assert (
        calls[0][1]["timeout"]
        == "10000ms"
    )


def test_lexical_timeout_is_fail_soft(
    monkeypatch,
) -> None:

    calls = []

    monkeypatch.setattr(
        hybrid,
        "_fts_index_available",
        lambda: True,
    )

    monkeypatch.setattr(
        hybrid,
        "SessionLocal",
        lambda: _FakeSession(
            calls,
            fail_fts=True,
        ),
    )

    rows, warnings = (
        hybrid._lexical_candidates(
            query="project alpha",
            source_type="all",
            candidate_limit=50,
        )
    )

    assert rows == []

    assert len(warnings) == 1

    assert (
        "failed and was skipped"
        in warnings[0]
    )

    assert (
        "statement timeout"
        in warnings[0]
    )
