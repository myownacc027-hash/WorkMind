from pathlib import Path


def test_migration_uses_concurrent_fts_index() -> None:
    source = Path(
        "migrations/versions/0004_hybrid_search_fts.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "0004_hybrid_search_fts"' in source
    assert 'down_revision = "0003_attachment_text"' in source
    assert "CREATE INDEX CONCURRENTLY" in source
    assert "to_tsvector" in source
    assert "'simple'::regconfig" in source


def test_primary_mcp_tools_are_registered() -> None:
    source = Path(
        "apps/mcp_server/server.py"
    ).read_text(encoding="utf-8")
    assert "def search_workmind(" in source
    assert "def get_workmind_context_bundle(" in source
    assert "Use search_workmind first" in source


def test_hybrid_search_groups_by_source() -> None:
    source = Path(
        "packages/search/hybrid.py"
    ).read_text(encoding="utf-8")
    assert "sources.setdefault" in source
    assert '"matched_chunks": matched_chunks' in source
    assert "unique_sources_before_filters" in source


def test_existing_search_tools_remain() -> None:
    source = Path(
        "apps/mcp_server/server.py"
    ).read_text(encoding="utf-8")
    assert "def search_emails(" in source
    assert "def search_exact(" in source
    assert "def search_attachments(" in source



def test_search_quality_gate_and_diversity_are_present() -> None:
    source = Path(
        "packages/search/hybrid.py"
    ).read_text(encoding="utf-8")
    assert "def _quality_gate(" in source
    assert "def _diversity_key(" in source
    assert "collapsed_thread_or_file_duplicates" in source
    assert "primary_anchor_missing" in source


def test_natural_language_lexical_search_is_strict(
    monkeypatch,
) -> None:
    import packages.search.hybrid as hybrid

    captured: dict[str, object] = {}

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeSession:
        def __enter__(self):
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

    rows, warnings = (
        hybrid._lexical_candidates(
            query="project alpha",
            source_type="all",
            candidate_limit=50,
        )
    )

    assert rows == []
    assert warnings == []

    sql = str(
        captured["sql"]
    )

    params = captured[
        "params"
    ]

    # Natural-language lexical retrieval must use
    # the strict websearch query for both ranking
    # and candidate matching.
    assert (
        "@@ search_query.strict_query"
        in sql
    )

    # relaxed_query may still be constructed in the
    # shared CTE for identifier support, but it must
    # not participate in natural-language matching.
    assert (
        "@@ search_query.relaxed_query"
        not in sql
    )

    assert (
        "search_query.strict_query"
        in sql
    )

    assert (
        "ORDER BY"
        in sql
        and "lexical_score DESC"
        in sql
    )

    assert (
        "LIMIT"
        in sql
    )

    assert (
        params["query"]
        == "project alpha"
    )

    assert (
        params["source_type"]
        == "all"
    )

    assert (
        params["candidate_limit"]
        == 50
    )


def test_cross_thread_excerpt_deduplication_is_present() -> None:
    source = Path("packages/search/hybrid.py").read_text(encoding="utf-8")
    assert "def _normalized_excerpt_signature(" in source
    assert '"matched_excerpt"' in source
    assert "_diversity_keys(item)" in source



def test_lexical_document_candidates_are_merged() -> None:
    source = Path(
        "packages/search/hybrid.py"
    ).read_text(encoding="utf-8")
    assert (
        '{"email", "attachment", "document"}'
        in source
    )
