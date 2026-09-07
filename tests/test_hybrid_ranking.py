from packages.search.ranking import (
    anchor_tokens,
    is_identifier_query,
    primary_anchor_token,
    query_tokens,
    rank_signals,
)


def test_identifier_query_detection() -> None:
    assert is_identifier_query("Б24 №189422") is True
    assert is_identifier_query("010460406000123421ABCD") is True
    assert is_identifier_query(
        "почему возникла проблема с приемкой"
    ) is False


def test_query_tokens_keep_identifiers() -> None:
    assert query_tokens("Б24 №189422 KIZ-123") == (
        "б24",
        "189422",
        "kiz-123",
    )


def test_exact_identifier_match_gets_strong_score() -> None:
    exact = rank_signals(
        query="Б24 №189422",
        content="Исправление по задаче Б24 №189422 выполнено.",
        metadata_text="Кластерный отбор",
        semantic_score=0.42,
        lexical_score=1.0,
        semantic_rank=20,
        lexical_rank=1,
        mode="hybrid",
    )
    semantic_only = rank_signals(
        query="Б24 №189422",
        content="Обсуждалась похожая ошибка отбора.",
        metadata_text="Кластерный отбор",
        semantic_score=0.88,
        lexical_score=0.0,
        semantic_rank=1,
        lexical_rank=None,
        mode="hybrid",
    )
    assert exact.final > semantic_only.final
    assert "exact_phrase" in exact.reasons
    assert "identifier_query" in exact.reasons


def test_natural_language_keeps_semantic_weight() -> None:
    result = rank_signals(
        query="почему сотрудники не могли продолжить работу",
        content=(
            "Сотрудники не могли выполнить текущую задачу "
            "без вмешательства администратора."
        ),
        metadata_text="Зависание ТСД",
        semantic_score=0.9,
        lexical_score=0.25,
        semantic_rank=1,
        lexical_rank=12,
        mode="hybrid",
    )
    assert result.final > 0.5
    assert "semantic" in result.reasons



def test_short_duration_inside_sentence_is_not_identifier() -> None:
    assert is_identifier_query(
        "технологическое окно 24 часа возражения филиалов"
    ) is False
    assert is_identifier_query("2544") is True


def test_primary_anchor_prefers_first_explicit_entity() -> None:
    query = "КИЗ отсутствуют в WMS но присутствуют в ERP"
    assert anchor_tokens(query) == ("киз", "wms", "erp")
    assert primary_anchor_token(query) == "киз"


def test_missing_primary_anchor_is_penalized() -> None:
    result = rank_signals(
        query="КИЗ отсутствуют в WMS но присутствуют в ERP",
        content="Интеграция между ERP и WMS работает.",
        metadata_text="Документ интеграции",
        semantic_score=0.8,
        lexical_score=0.9,
        semantic_rank=1,
        lexical_rank=1,
        mode="hybrid",
    )
    assert result.primary_anchor == 0.0
    assert result.final < 0.5
    assert "primary_anchor_missing" in result.reasons
