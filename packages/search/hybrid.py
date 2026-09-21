from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from typing import Any

from qdrant_client import models as qmodels
from sqlalchemy import bindparam, func, select, text

from packages.common.config import Settings, get_settings
from packages.database.models import (
    Attachment,
    EmailMessage,
    ExternalDocument,
)
from packages.database.session import SessionLocal
from packages.processing.embeddings import embed_texts
from packages.search.qdrant import get_qdrant_client
from packages.search.ranking import (
    anchor_tokens,
    compound_identifier_match_span,
    informative_query_tokens,
    is_identifier_query,
    normalize_query,
    parse_compound_identifier,
    primary_anchor_token,
    query_tokens,
    rank_signals,
    weighted_query_coverage,
)

FTS_INDEX_NAME = "ix_document_chunks_text_fts_simple"
VALID_MODES = {"hybrid", "semantic", "lexical"}
VALID_SOURCE_TYPES = {"all", "email", "attachment", "document"}


LEXICAL_RESCUE_MAX_VARIANTS = 3
LEXICAL_RESCUE_ACCEPT_MAX_ROWS = 25
LEXICAL_RESCUE_PROBE_LIMIT = (
    LEXICAL_RESCUE_ACCEPT_MAX_ROWS + 1
)
LEXICAL_RESCUE_MIN_COVERAGE = 0.34

LEXICAL_RESCUE_GENERIC_QUERY_TOKENS = frozenset(
    {
        "какой",
        "какая",
        "какие",
        "какое",
        "каков",
        "какова",
        "каковы",
        "почему",
        "где",
        "когда",
        "кто",
        "что",
        "сколько",
        "действует",
        "действуют",
        "может",
        "могут",
        "быть",
        "находится",
        "находятся",
        "расположен",
        "расположены",
        "указан",
        "указана",
        "указаны",
        "указано",
        "перечислен",
        "перечислена",
        "перечислены",
        "перечислено",
        "письмо",
        "письме",
        "новый",
        "новая",
        "новые",
        "новое",
    }
)
LEXICAL_STATEMENT_TIMEOUT_MS = 10_000


class HybridSearchError(ValueError):
    pass


def _parse_datetime(
    value: str | None,
    *,
    end_of_day: bool = False,
) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            if end_of_day:
                parsed_date += timedelta(days=1)
            return datetime.combine(
                parsed_date,
                datetime_time.min,
                tzinfo=UTC,
            )
        parsed = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError as exc:
        raise HybridSearchError(
            f"Invalid ISO date/datetime: {value!r}"
        ) from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _occurred_at(message: EmailMessage) -> datetime | None:
    return message.received_at or message.sent_at


def _safe_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _source_key(
    source_type: str,
    source_id: uuid.UUID,
) -> tuple[str, uuid.UUID]:
    return source_type, source_id


def _empty_source(
    source_type: str,
    source_id: uuid.UUID,
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": source_id,
        "semantic_rank": None,
        "semantic_score": None,
        "lexical_rank": None,
        "lexical_score_raw": None,
        "chunks": {},
    }


def _merge_chunk(
    source: dict[str, Any],
    *,
    chunk_id: Any,
    chunk_index: Any,
    chunk_text: str,
    semantic_rank: int | None = None,
    semantic_score: float | None = None,
    lexical_rank: int | None = None,
    lexical_score_raw: float | None = None,
) -> None:
    chunk_key = str(
        chunk_id
        or f"{source['source_type']}:{source['source_id']}:"
        f"{chunk_index}:{len(source['chunks'])}"
    )
    chunk = source["chunks"].setdefault(
        chunk_key,
        {
            "chunk_id": str(chunk_id) if chunk_id else None,
            "chunk_index": (
                int(chunk_index)
                if chunk_index is not None
                else None
            ),
            "text": chunk_text or "",
            "semantic_rank": None,
            "semantic_score": None,
            "lexical_rank": None,
            "lexical_score_raw": None,
        },
    )
    if chunk_text and not chunk["text"]:
        chunk["text"] = chunk_text
    if semantic_rank is not None:
        current = chunk["semantic_rank"]
        if current is None or semantic_rank < current:
            chunk["semantic_rank"] = semantic_rank
        chunk["semantic_score"] = max(
            float(semantic_score or 0.0),
            float(chunk["semantic_score"] or 0.0),
        )
    if lexical_rank is not None:
        current = chunk["lexical_rank"]
        if current is None or lexical_rank < current:
            chunk["lexical_rank"] = lexical_rank
        chunk["lexical_score_raw"] = max(
            float(lexical_score_raw or 0.0),
            float(chunk["lexical_score_raw"] or 0.0),
        )


def _eligible_source_types(
    source_type: str,
    *,
    sender: str | None,
    filename: str | None,
) -> tuple[str, ...]:
    requested = (
        {
            "email",
            "attachment",
            "document",
        }
        if source_type == "all"
        else {
            source_type,
        }
    )

    # Documents have no sender metadata.
    if sender:
        requested.discard(
            "document"
        )

    # Email itself has no filename.
    if filename:
        requested.discard(
            "email"
        )

    return tuple(
        name
        for name in (
            "email",
            "attachment",
            "document",
        )
        if name in requested
    )


def _metadata_eligible_source_ids(
    *,
    source_type: str,
    sender: str | None,
    filename: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict[
    str,
    tuple[uuid.UUID, ...],
] | None:
    """
    Resolve metadata filters to source IDs BEFORE retrieval.

    None means that no metadata filter is active.
    An empty mapping means that the active filters cannot
    match any source type.
    """

    if not (
        sender
        or filename
        or date_from
        or date_to
    ):
        return None

    eligible_types = (
        _eligible_source_types(
            source_type,
            sender=sender,
            filename=filename,
        )
    )

    result: dict[
        str,
        tuple[uuid.UUID, ...],
    ] = {}

    if not eligible_types:
        return result

    sender_value = (
        sender.casefold().strip()
        if sender
        else None
    )

    filename_value = (
        filename.casefold().strip()
        if filename
        else None
    )

    with SessionLocal() as session:

        # -------------------------------------------------------------
        # Email
        # -------------------------------------------------------------

        if "email" in eligible_types:
            statement = select(
                EmailMessage.id
            )

            if sender_value is not None:
                sender_text = func.lower(
                    func.concat_ws(
                        " ",
                        func.coalesce(
                            EmailMessage.sender_name,
                            "",
                        ),
                        func.coalesce(
                            EmailMessage.sender_email,
                            "",
                        ),
                    )
                )

                statement = (
                    statement.where(
                        func.strpos(
                            sender_text,
                            sender_value,
                        )
                        > 0
                    )
                )

            occurred = func.coalesce(
                EmailMessage.received_at,
                EmailMessage.sent_at,
            )

            if date_from is not None:
                statement = (
                    statement.where(
                        occurred
                        >= date_from
                    )
                )

            if date_to is not None:
                statement = (
                    statement.where(
                        occurred
                        < date_to
                    )
                )

            result["email"] = tuple(
                session.scalars(
                    statement
                ).all()
            )

        # -------------------------------------------------------------
        # Attachment
        # -------------------------------------------------------------

        if (
            "attachment"
            in eligible_types
        ):
            statement = (
                select(
                    Attachment.id
                )
                .join(
                    EmailMessage,
                    EmailMessage.id
                    == Attachment.message_id,
                )
            )

            if sender_value is not None:
                sender_text = func.lower(
                    func.concat_ws(
                        " ",
                        func.coalesce(
                            EmailMessage.sender_name,
                            "",
                        ),
                        func.coalesce(
                            EmailMessage.sender_email,
                            "",
                        ),
                    )
                )

                statement = (
                    statement.where(
                        func.strpos(
                            sender_text,
                            sender_value,
                        )
                        > 0
                    )
                )

            if (
                filename_value
                is not None
            ):
                statement = (
                    statement.where(
                        func.strpos(
                            func.lower(
                                Attachment.filename
                            ),
                            filename_value,
                        )
                        > 0
                    )
                )

            occurred = func.coalesce(
                EmailMessage.received_at,
                EmailMessage.sent_at,
            )

            if date_from is not None:
                statement = (
                    statement.where(
                        occurred
                        >= date_from
                    )
                )

            if date_to is not None:
                statement = (
                    statement.where(
                        occurred
                        < date_to
                    )
                )

            result[
                "attachment"
            ] = tuple(
                session.scalars(
                    statement
                ).all()
            )

        # -------------------------------------------------------------
        # External document
        # -------------------------------------------------------------

        if (
            "document"
            in eligible_types
        ):
            statement = select(
                ExternalDocument.id
            )

            if (
                filename_value
                is not None
            ):
                statement = (
                    statement.where(
                        func.strpos(
                            func.lower(
                                ExternalDocument
                                .original_filename
                            ),
                            filename_value,
                        )
                        > 0
                    )
                )

            if date_from is not None:
                statement = (
                    statement.where(
                        ExternalDocument
                        .source_date
                        >= date_from
                    )
                )

            if date_to is not None:
                statement = (
                    statement.where(
                        ExternalDocument
                        .source_date
                        < date_to
                    )
                )

            result[
                "document"
            ] = tuple(
                session.scalars(
                    statement
                ).all()
            )

    return result


def _flatten_eligible_source_ids(
    value: dict[
        str,
        tuple[uuid.UUID, ...],
    ]
    | None,
) -> tuple[uuid.UUID, ...] | None:
    if value is None:
        return None

    unique_ids = {
        source_id
        for source_ids
        in value.values()
        for source_id
        in source_ids
    }

    return tuple(
        sorted(
            unique_ids,
            key=str,
        )
    )


def _semantic_candidates(
    *,
    query: str,
    source_type: str,
    candidate_limit: int,
    min_score: float,
    eligible_source_ids: (
        tuple[uuid.UUID, ...]
        | None
    ) = None,
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    warnings: list[str] = []

    # An active metadata filter resolved to zero sources.
    # Do not calculate an embedding and do not ask Qdrant.
    if (
        eligible_source_ids
        is not None
        and not eligible_source_ids
    ):
        return [], warnings

    settings = get_settings()

    try:
        client = get_qdrant_client()

        if not client.collection_exists(
            settings.qdrant_collection
        ):
            return [], [
                "Qdrant collection does not exist; "
                "semantic channel was skipped."
            ]

        must_conditions: list[Any] = []

        if source_type != "all":
            must_conditions.append(
                qmodels.FieldCondition(
                    key="source_type",
                    match=qmodels.MatchValue(
                        value=source_type,
                    ),
                )
            )

        if (
            eligible_source_ids
            is not None
        ):
            collection_info = (
                client.get_collection(
                    settings.qdrant_collection
                )
            )

            payload_schema = (
                getattr(
                    collection_info,
                    "payload_schema",
                    None,
                )
                or {}
            )

            if (
                "source_id"
                not in payload_schema
            ):
                return [], [
                    "Qdrant source_id payload "
                    "index is missing; semantic "
                    "metadata pushdown was "
                    "skipped."
                ]

            must_conditions.append(
                qmodels.FieldCondition(
                    key="source_id",
                    match=qmodels.MatchAny(
                        any=[
                            str(source_id)
                            for source_id
                            in eligible_source_ids
                        ],
                    ),
                )
            )

        query_filter = (
            qmodels.Filter(
                must=must_conditions
            )
            if must_conditions
            else None
        )

        vector = embed_texts(
            [
                query,
            ]
        )[0]

        response = client.query_points(
            collection_name=(
                settings.qdrant_collection
            ),
            query=vector,
            query_filter=query_filter,
            limit=candidate_limit,
            score_threshold=(
                min_score
                if min_score > 0
                else None
            ),
            with_payload=True,
            search_params=(
                qmodels.SearchParams(
                    hnsw_ef=(
                        settings.hybrid_search_hnsw_ef
                    ),
                    exact=False,
                )
            ),
        )

        return [
            {
                "rank": rank,
                "score": float(
                    point.score
                ),
                "point_id": str(
                    point.id
                ),
                "payload": (
                    point.payload
                    or {}
                ),
            }
            for rank, point
            in enumerate(
                response.points,
                start=1,
            )
        ], warnings

    except Exception as exc:
        warnings.append(
            "Semantic search channel "
            "failed and was skipped: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return [], warnings


def _fts_index_available() -> bool:
    with SessionLocal() as session:
        return bool(
            session.scalar(
                text(
                    "SELECT to_regclass("
                    "'public.ix_document_chunks_text_fts_simple'"
                    ") IS NOT NULL"
                )
            )
        )


def _apply_lexical_statement_timeout(
    session,
) -> None:
    """
    Bound PostgreSQL lexical retrieval to the current transaction.

    _lexical_candidates() is fail-soft, so a pathological FTS query
    becomes a warning instead of blocking WorkMind indefinitely.
    """
    session.execute(
        text(
            "SELECT set_config("
            "'statement_timeout', "
            ":timeout, "
            "true)"
        ),
        {
            "timeout": (
                f"{LEXICAL_STATEMENT_TIMEOUT_MS}ms"
            )
        },
    )

def _lexical_candidates(
    *,
    query: str,
    source_type: str,
    candidate_limit: int,
    eligible_source_ids: (
        tuple[uuid.UUID, ...]
        | None
    ) = None,
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    if not _fts_index_available():
        return [], [
            "PostgreSQL hybrid-search "
            "index is missing; run "
            "`alembic upgrade head`."
        ]

    # Active metadata filters resolved to no sources.
    if (
        eligible_source_ids
        is not None
        and not eligible_source_ids
    ):
        return [], []

    source_id_filter_sql = (
        """
        AND chunks.source_id
            IN :eligible_source_ids
        """
        if eligible_source_ids
        is not None
        else ""
    )

    def prepare_statement(
        statement,
    ):
        if (
            eligible_source_ids
            is not None
        ):
            statement = (
                statement.bindparams(
                    bindparam(
                        "eligible_source_ids",
                        expanding=True,
                    )
                )
            )

        return statement

    def add_source_params(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            eligible_source_ids
            is not None
        ):
            params[
                "eligible_source_ids"
            ] = (
                eligible_source_ids
            )

        return params

    identifier = is_identifier_query(
        query
    )

    compound_identifier = (
        parse_compound_identifier(
            query
        )
        if identifier
        else None
    )

    # ================================================================
    # Compound identifiers
    # ================================================================

    if compound_identifier is not None:
        identifier_pattern = (
            r"(^|[^0-9A-Za-zА-Яа-яЁё_])"
            + re.escape(
                compound_identifier.prefix
            )
            + (
                r"(?:"
                r"[ \t\u00a0]*"
                r"(?:[_‐-‒–—―-]|№)"
                r"[ \t\u00a0]*"
                r"|"
                r"[ \t\u00a0]+"
                r")"
            )
            + re.escape(
                compound_identifier.number
            )
            + (
                r"($|"
                r"[^0-9A-Za-zА-Яа-яЁё_])"
            )
        )

        statement = text(
            f"""
            WITH search_query AS (
                SELECT
                    plainto_tsquery(
                        'simple'::regconfig,
                        :identifier_prefix
                    ) AS prefix_query
            )

            SELECT
                chunks.id,
                chunks.source_type,
                chunks.source_id,
                chunks.chunk_index,
                chunks.text,

                ts_rank_cd(
                    to_tsvector(
                        'simple'::regconfig,
                        COALESCE(
                            chunks.text,
                            ''::text
                        )
                    ),
                    search_query.prefix_query,
                    32
                ) AS lexical_score

            FROM document_chunks
                AS chunks

            CROSS JOIN search_query

            WHERE
                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(
                        chunks.text,
                        ''::text
                    )
                )
                @@ search_query.prefix_query

                AND COALESCE(
                    chunks.text,
                    ''::text
                )
                ~* :identifier_pattern

                AND (
                    :source_type = 'all'
                    OR chunks.source_type
                    = :source_type
                )

                {source_id_filter_sql}

            ORDER BY
                lexical_score DESC,
                chunks.id

            LIMIT :candidate_limit
            """
        )

        statement = prepare_statement(
            statement
        )

        params = add_source_params(
            {
                "identifier_prefix": (
                    compound_identifier
                    .prefix
                ),
                "identifier_pattern": (
                    identifier_pattern
                ),
                "source_type": (
                    source_type
                ),
                "candidate_limit": (
                    candidate_limit
                ),
            }
        )

        try:
            with SessionLocal() as session:
                _apply_lexical_statement_timeout(
                    session
                )
                rows = (
                    session.execute(
                        statement,
                        params,
                    )
                    .mappings()
                    .all()
                )

            return [
                {
                    "rank": rank,
                    "chunk_id": row["id"],
                    "source_type": (
                        row[
                            "source_type"
                        ]
                    ),
                    "source_id": (
                        row[
                            "source_id"
                        ]
                    ),
                    "chunk_index": (
                        row[
                            "chunk_index"
                        ]
                    ),
                    "text": (
                        row["text"]
                    ),
                    "score": float(
                        row[
                            "lexical_score"
                        ]
                        or 0.0
                    ),
                }
                for rank, row
                in enumerate(
                    rows,
                    start=1,
                )
            ], []

        except Exception as exc:
            return [], [
                "Compound identifier "
                "lexical search failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            ]

    # ================================================================
    # Other identifiers / natural-language lexical search
    # ================================================================

    relaxed_terms: list[str] = []
    seen_terms: set[str] = set()

    for token in (
        informative_query_tokens(
            query
        )
    ):
        for term in re.findall(
            r"[^\W_]+",
            token,
            flags=re.UNICODE,
        ):
            folded = term.casefold()

            if (
                len(folded) < 2
                or folded in seen_terms
            ):
                continue

            seen_terms.add(
                folded
            )

            relaxed_terms.append(
                (
                    f"{folded}:*"
                    if (
                        len(folded) >= 4
                        and not folded.isdigit()
                    )
                    else folded
                )
            )

    relaxed_query = (
        " | ".join(
            relaxed_terms
        )
        or "workmindnomatch"
    )

    if identifier:
        score_sql = """
            GREATEST(
                ts_rank_cd(
                    to_tsvector(
                        'simple'::regconfig,
                        COALESCE(
                            chunks.text,
                            ''::text
                        )
                    ),
                    search_query.strict_query,
                    32
                ),
                0.65 * ts_rank_cd(
                    to_tsvector(
                        'simple'::regconfig,
                        COALESCE(
                            chunks.text,
                            ''::text
                        )
                    ),
                    search_query.relaxed_query,
                    32
                )
            )
        """

        match_sql = """
            (
                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(
                        chunks.text,
                        ''::text
                    )
                )
                @@ search_query.strict_query

                OR

                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(
                        chunks.text,
                        ''::text
                    )
                )
                @@ search_query.relaxed_query
            )
        """

    else:
        score_sql = """
            ts_rank_cd(
                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(
                        chunks.text,
                        ''::text
                    )
                ),
                search_query.strict_query,
                32
            )
        """

        match_sql = """
            to_tsvector(
                'simple'::regconfig,
                COALESCE(
                    chunks.text,
                    ''::text
                )
            )
            @@ search_query.strict_query
        """

    statement = text(
        f"""
        WITH search_query AS (
            SELECT
                websearch_to_tsquery(
                    'simple'::regconfig,
                    :query
                ) AS strict_query,

                to_tsquery(
                    'simple'::regconfig,
                    :relaxed_query
                ) AS relaxed_query
        )

        SELECT
            chunks.id,
            chunks.source_type,
            chunks.source_id,
            chunks.chunk_index,
            chunks.text,
            {score_sql}
                AS lexical_score

        FROM document_chunks
            AS chunks

        CROSS JOIN search_query

        WHERE
            {match_sql}

            AND (
                :source_type = 'all'
                OR chunks.source_type
                = :source_type
            )

            {source_id_filter_sql}

        ORDER BY
            lexical_score DESC,
            chunks.id

        LIMIT :candidate_limit
        """
    )

    statement = prepare_statement(
        statement
    )

    params = add_source_params(
        {
            "query": query,
            "relaxed_query": (
                relaxed_query
            ),
            "source_type": (
                source_type
            ),
            "candidate_limit": (
                candidate_limit
            ),
        }
    )

    try:
        with SessionLocal() as session:
            _apply_lexical_statement_timeout(
                session
            )
            rows = (
                session.execute(
                    statement,
                    params,
                )
                .mappings()
                .all()
            )

        return [
            {
                "rank": rank,
                "chunk_id": (
                    row["id"]
                ),
                "source_type": (
                    row[
                        "source_type"
                    ]
                ),
                "source_id": (
                    row["source_id"]
                ),
                "chunk_index": (
                    row[
                        "chunk_index"
                    ]
                ),
                "text": row["text"],
                "score": float(
                    row[
                        "lexical_score"
                    ]
                    or 0.0
                ),
            }
            for rank, row
            in enumerate(
                rows,
                start=1,
            )
        ], []

    except Exception as exc:
        return [], [
            "Lexical search channel "
            "failed and was skipped: "
            f"{type(exc).__name__}: "
            f"{exc}"
        ]


def _enrich_sources(
    sources: dict[tuple[str, uuid.UUID], dict[str, Any]],
) -> None:
    email_ids = [
        source_id
        for source_type, source_id in sources
        if source_type == "email"
    ]
    attachment_ids = [
        source_id
        for source_type, source_id in sources
        if source_type == "attachment"
    ]
    document_ids = [
        source_id
        for source_type, source_id in sources
        if source_type == "document"
    ]

    with SessionLocal() as session:
        if email_ids:
            messages = list(
                session.scalars(
                    select(EmailMessage).where(
                        EmailMessage.id.in_(email_ids)
                    )
                )
            )
            for message in messages:
                key = _source_key("email", message.id)
                source = sources.get(key)
                if source is None:
                    continue
                source["metadata"] = {
                    "source_ref": (
                        f"workmind://email/{message.id}"
                    ),
                    "parent_source_id": None,
                    "parent_source_ref": None,
                    "thread_id": (
                        str(message.thread_id)
                        if message.thread_id
                        else None
                    ),
                    "subject": message.subject,
                    "filename": None,
                    "mime_type": None,
                    "sender_name": message.sender_name,
                    "sender_email": message.sender_email,
                    "date": _iso(_occurred_at(message)),
                    "date_value": _occurred_at(message),
                    "folder_path": message.folder_path,
                    "has_attachments": (
                        message.has_attachments
                    ),
                }

        if attachment_ids:
            rows = session.execute(
                select(Attachment, EmailMessage)
                .join(
                    EmailMessage,
                    EmailMessage.id
                    == Attachment.message_id,
                )
                .where(Attachment.id.in_(attachment_ids))
            ).all()
            for attachment, message in rows:
                key = _source_key(
                    "attachment",
                    attachment.id,
                )
                source = sources.get(key)
                if source is None:
                    continue
                source["metadata"] = {
                    "source_ref": (
                        "workmind://attachment/"
                        f"{attachment.id}"
                    ),
                    "parent_source_id": str(message.id),
                    "parent_source_ref": (
                        f"workmind://email/{message.id}"
                    ),
                    "thread_id": (
                        str(message.thread_id)
                        if message.thread_id
                        else None
                    ),
                    "subject": message.subject,
                    "filename": attachment.filename,
                    "mime_type": attachment.mime_type,
                    "sender_name": message.sender_name,
                    "sender_email": message.sender_email,
                    "date": _iso(_occurred_at(message)),
                    "date_value": _occurred_at(message),
                    "folder_path": message.folder_path,
                    "has_attachments": True,
                    "attachment_status": (
                        attachment.processing_status
                    ),
                    "extraction_method": (
                        attachment.extraction_method
                    ),
                    "content_hash": attachment.content_hash,
                }


        if document_ids:
            documents = list(
                session.scalars(
                    select(ExternalDocument).where(
                        ExternalDocument.id.in_(document_ids)
                    )
                )
            )
            for document in documents:
                key = _source_key(
                    "document",
                    document.id,
                )
                source = sources.get(key)
                if source is None:
                    continue
                source["metadata"] = {
                    "source_ref": (
                        "workmind://document/"
                        f"{document.id}"
                    ),
                    "parent_source_id": None,
                    "parent_source_ref": None,
                    "thread_id": None,
                    "subject": document.original_filename,
                    "filename": document.original_filename,
                    "mime_type": document.mime_type,
                    "sender_name": None,
                    "sender_email": None,
                    "date": _iso(document.source_date),
                    "date_value": document.source_date,
                    "folder_path": None,
                    "archive_path": document.archive_path,
                    "has_attachments": False,
                    "document_status": (
                        document.processing_status
                    ),
                    "extraction_method": (
                        document.extraction_method
                    ),
                    "content_hash": document.content_hash,
                }


def _source_passes_filters(
    source: dict[str, Any],
    *,
    sender: str | None,
    filename: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> bool:
    metadata = source.get("metadata")
    if not metadata:
        return False

    if sender:
        if source["source_type"] == "document":
            return False
        folded = sender.casefold().strip()
        sender_text = " ".join(
            [
                str(metadata.get("sender_name") or ""),
                str(metadata.get("sender_email") or ""),
            ]
        ).casefold()
        if folded not in sender_text:
            return False

    if filename:
        if source["source_type"] not in {
            "attachment",
            "document",
        }:
            return False
        if filename.casefold().strip() not in str(
            metadata.get("filename") or ""
        ).casefold():
            return False

    occurred = metadata.get("date_value")
    if date_from and (
        occurred is None or occurred < date_from
    ):
        return False
    if date_to and (
        occurred is None or occurred >= date_to
    ):
        return False
    return True



def _lexical_rescue_variants(
    query: str,
) -> tuple[str, ...]:
    tokens = list(
        informative_query_tokens(
            query
        )
    )

    anchors = list(
        anchor_tokens(
            query
        )
    )

    anchor_set = set(
        anchors
    )

    content = [
        token
        for token in tokens
        if (
            token
            not in (
                LEXICAL_RESCUE_GENERIC_QUERY_TOKENS
            )
            and len(token) >= 3
        )
    ]

    variants: list[str] = []


    def add(
        parts: list[str],
    ) -> None:
        clean: list[str] = []
        seen: set[str] = set()

        for part in parts:
            folded = (
                part.casefold()
                .strip()
            )

            if (
                not folded
                or folded in seen
            ):
                continue

            seen.add(
                folded
            )

            clean.append(
                folded
            )

        if len(clean) < 2:
            return

        value = " ".join(
            clean
        )

        if (
            value.casefold()
            == query.casefold().strip()
        ):
            return

        if value in variants:
            return

        variants.append(
            value
        )


    if len(anchors) >= 2:
        add(
            anchors[:4]
        )

    elif len(anchors) == 1:
        non_anchor = [
            token
            for token in content
            if token not in anchor_set
        ]

        if non_anchor:
            add(
                [
                    anchors[0],
                    non_anchor[0],
                ]
            )


    add(
        content[:4]
    )


    longest = sorted(
        content,
        key=lambda token: (
            len(token),
            -tokens.index(token),
        ),
        reverse=True,
    )

    add(
        longest[:3]
    )


    return tuple(
        variants[
            :LEXICAL_RESCUE_MAX_VARIANTS
        ]
    )


def _lexical_rescue_candidates(
    *,
    query: str,
    source_type: str,
    candidate_limit: int,
    eligible_source_ids: (
        tuple[uuid.UUID, ...]
        | None
    ) = None,
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    if is_identifier_query(
        query
    ):
        return [], []


    variants = (
        _lexical_rescue_variants(
            query
        )
    )

    accepted: dict[
        str,
        dict[str, Any],
    ] = {}

    warnings: list[str] = []


    for variant in variants:
        # A generated query must never enter the
        # relaxed identifier branch.
        if is_identifier_query(
            variant
        ):
            continue


        rows, channel_warnings = (
            _lexical_candidates(
                query=variant,
                source_type=source_type,
                candidate_limit=(
                    LEXICAL_RESCUE_PROBE_LIMIT
                ),
                eligible_source_ids=(
                    eligible_source_ids
                ),
            )
        )

        warnings.extend(
            channel_warnings
        )


        # We intentionally ask for 26 rows.
        # If 26 arrive, this reduced query is
        # broader than the accepted maximum of 25.
        if (
            len(rows)
            > LEXICAL_RESCUE_ACCEPT_MAX_ROWS
        ):
            continue


        for variant_rank, row in enumerate(
            rows,
            start=1,
        ):
            coverage = float(
                weighted_query_coverage(
                    query,
                    str(
                        row.get("text")
                        or ""
                    ),
                )
            )


            if (
                coverage
                < LEXICAL_RESCUE_MIN_COVERAGE
            ):
                continue


            chunk_id = str(
                row.get("chunk_id")
                or ""
            )

            if not chunk_id:
                continue


            candidate = {
                "row": dict(row),
                "coverage": coverage,
                "variant_rank": (
                    variant_rank
                ),
                "variant_cardinality": (
                    len(rows)
                ),
            }


            current = accepted.get(
                chunk_id
            )


            if current is None:
                accepted[
                    chunk_id
                ] = candidate

                continue


            current_key = (
                float(
                    current[
                        "coverage"
                    ]
                ),
                -int(
                    current[
                        "variant_cardinality"
                    ]
                ),
                -int(
                    current[
                        "variant_rank"
                    ]
                ),
            )

            candidate_key = (
                coverage,
                -len(rows),
                -variant_rank,
            )


            if (
                candidate_key
                > current_key
            ):
                accepted[
                    chunk_id
                ] = candidate


    ordered = sorted(
        accepted.values(),
        key=lambda item: (
            float(
                item[
                    "coverage"
                ]
            ),
            -int(
                item[
                    "variant_cardinality"
                ]
            ),
            -int(
                item[
                    "variant_rank"
                ]
            ),
            float(
                item[
                    "row"
                ].get(
                    "score"
                )
                or 0.0
            ),
        ),
        reverse=True,
    )


    output: list[
        dict[str, Any]
    ] = []


    for rank, item in enumerate(
        ordered[
            :candidate_limit
        ],
        start=1,
    ):
        row = dict(
            item["row"]
        )

        row["rank"] = rank

        output.append(
            row
        )


    return (
        output,
        warnings,
    )

def _excerpt(
    value: str,
    *,
    query: str,
    limit: int,
) -> str:
    clean = value.strip()

    if len(clean) <= limit:
        return clean

    identifier_span = (
        compound_identifier_match_span(
            query,
            clean,
        )
        if is_identifier_query(query)
        else None
    )

    if identifier_span is not None:
        position = identifier_span[0]

    else:
        folded = clean.casefold()

        normalized_query = normalize_query(
            query
        )

        position = folded.find(
            normalized_query
        )

        if position < 0:
            positions = [
                folded.find(token)
                for token
                in query_tokens(query)
                if folded.find(token) >= 0
            ]

            position = (
                min(positions)
                if positions
                else 0
            )

    before = min(
        240,
        max(
            0,
            limit // 4,
        ),
    )

    start = max(
        0,
        position - before,
    )

    end = min(
        len(clean),
        start + limit,
    )

    if end - start < limit:
        start = max(
            0,
            end - limit,
        )

    excerpt = clean[
        start:end
    ].strip()

    if start > 0:
        excerpt = "… " + excerpt

    if end < len(clean):
        excerpt += " …"

    return excerpt


def _quality_gate(
    *,
    query: str,
    mode: str,
    signals,
    best_chunk_signals,
    min_score: float,
) -> tuple[bool, str | None]:
    if (
        min_score > 0
        and signals.final < min_score
    ):
        return False, "below_min_score"

    identifier = is_identifier_query(
        query
    )

    compound_identifier = (
        parse_compound_identifier(query)
        if identifier
        else None
    )

    if compound_identifier is not None:
        # A compound identifier must occur
        # structurally as one identifier.
        # Co-occurrence of the two tokens
        # is explicitly insufficient.
        if signals.exact >= 0.999:
            return True, None

        return (
            False,
            "identifier_structure_missing",
        )

    primary_anchor = (
        primary_anchor_token(query)
    )

    if (
        primary_anchor is not None
        and best_chunk_signals.primary_anchor
        < 0.999
        and signals.metadata < 0.999
    ):
        return (
            False,
            "primary_anchor_missing",
        )

    informative_count = len(
        informative_query_tokens(
            query
        )
    )

    if identifier:
        if (
            signals.exact
            or best_chunk_signals.coverage
            >= 0.45
            or best_chunk_signals.lexical
            >= 0.40
        ):
            return True, None

        return (
            False,
            "weak_identifier_match",
        )

    if mode == "semantic":
        if (
            best_chunk_signals.semantic
            >= 0.42
            or best_chunk_signals.coverage
            >= 0.45
            or signals.exact
        ):
            return True, None

        return (
            False,
            "weak_semantic_match",
        )

    if informative_count >= 5:
        if (
            best_chunk_signals.coverage
            >= 0.34
            or (
                best_chunk_signals.semantic
                >= 0.55
                and best_chunk_signals.coverage
                >= 0.16
            )
            or signals.exact
        ):
            return True, None

        return (
            False,
            "low_query_coverage",
        )

    if informative_count >= 3:
        if (
            best_chunk_signals.coverage
            >= 0.34
            or best_chunk_signals.semantic
            >= 0.50
            or signals.exact
        ):
            return True, None

        return (
            False,
            "low_query_coverage",
        )

    return True, None


def _normalized_excerpt_signature(
    item: dict[str, Any],
) -> str | None:
    chunks = item.get("matched_chunks")
    if not isinstance(chunks, list) or not chunks:
        return None
    text_value = str(chunks[0].get("matched_text") or "")
    normalized = re.sub(r"\s+", " ", text_value.casefold()).strip()
    # Avoid collapsing very short generic excerpts.
    if len(normalized) < 180:
        return None
    return hashlib.sha256(
        normalized.encode("utf-8", errors="replace")
    ).hexdigest()


def _diversity_keys(
    item: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    keys: list[tuple[str, str]] = []
    if (
        item["source_type"] == "email"
        and item.get("thread_id")
    ):
        keys.append(("email_thread", str(item["thread_id"])))
    elif (
        item["source_type"] in {"attachment", "document"}
        and item.get("content_hash")
    ):
        keys.append(
            (
                "file_content",
                str(item["content_hash"]),
            )
        )
    else:
        keys.append(
            (
                str(item["source_type"]),
                str(item["source_id"]),
            )
        )

    excerpt_signature = _normalized_excerpt_signature(item)
    if excerpt_signature:
        keys.append(("matched_excerpt", excerpt_signature))
    return tuple(keys)


def _diversity_key(
    item: dict[str, Any],
) -> tuple[str, str]:
    """Backward-compatible primary diversity key."""
    return _diversity_keys(item)[0]


def _channel_plan(
    mode: str,
    *,
    identifier_query: bool,
) -> tuple[bool, bool]:
    """
    Return:
        (run_semantic, run_lexical)

    Identifier queries deliberately bypass
    embeddings and use the lexical/exact path.
    """
    run_semantic = (
        mode in {
            "hybrid",
            "semantic",
        }
        and not identifier_query
    )

    run_lexical = (
        mode in {
            "hybrid",
            "lexical",
        }
        or identifier_query
    )

    return (
        run_semantic,
        run_lexical,
    )


def search_hybrid(
    query: str,
    *,
    limit: int = 10,
    mode: str = "hybrid",
    source_type: str = "all",
    min_score: float = 0.0,
    sender: str | None = None,
    filename: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    snippets_per_source: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    clean_query = query.strip()
    if not clean_query:
        raise HybridSearchError("query must not be empty")

    clean_mode = mode.strip().lower()
    if clean_mode not in VALID_MODES:
        raise HybridSearchError(
            "mode must be one of: hybrid, semantic, lexical"
        )
    clean_source_type = source_type.strip().lower()
    if clean_source_type not in VALID_SOURCE_TYPES:
        raise HybridSearchError(
            "source_type must be one of: "
            "all, email, attachment, document"
        )

    safe_limit = max(1, min(int(limit), 50))
    safe_min_score = max(
        -1.0,
        min(float(min_score), 1.0),
    )
    start_date = _parse_datetime(date_from)
    end_date = _parse_datetime(
        date_to,
        end_of_day=True,
    )
    settings: Settings = get_settings()
    safe_snippet_count = max(
        1,
        min(
            int(
                snippets_per_source
                or settings.hybrid_search_snippets_per_source
            ),
            10,
        ),
    )
    candidate_limit = min(
        max(
            settings.hybrid_search_candidate_limit,
            safe_limit * 30,
        ),
        2000,
    )

    eligible_by_type = (
        _metadata_eligible_source_ids(
            source_type=clean_source_type,
            sender=sender,
            filename=filename,
            date_from=start_date,
            date_to=end_date,
        )
    )

    eligible_source_ids = (
        _flatten_eligible_source_ids(
            eligible_by_type
        )
    )

    warnings: list[str] = []
    semantic_rows: list[
        dict[str, Any]
    ] = []
    lexical_rows: list[
        dict[str, Any]
    ] = []

    identifier_query = (
        is_identifier_query(
            clean_query
        )
    )

    (
        run_semantic,
        run_lexical,
    ) = _channel_plan(
        clean_mode,
        identifier_query=(
            identifier_query
        ),
    )

    if run_semantic:
        (
            semantic_rows,
            channel_warnings,
        ) = _semantic_candidates(
            query=clean_query,
            source_type=clean_source_type,
            candidate_limit=candidate_limit,
            min_score=safe_min_score,
            eligible_source_ids=(
                eligible_source_ids
            ),
        )

        warnings.extend(
            channel_warnings
        )

    if run_lexical:
        (
            lexical_rows,
            channel_warnings,
        ) = _lexical_candidates(
            query=clean_query,
            source_type=clean_source_type,
            candidate_limit=candidate_limit,
            eligible_source_ids=(
                eligible_source_ids
            ),
        )

        warnings.extend(
            channel_warnings
        )

        if (
            not lexical_rows
            and not channel_warnings
            and not identifier_query
        ):
            (
                lexical_rows,
                channel_warnings,
            ) = _lexical_rescue_candidates(
                query=clean_query,
                source_type=clean_source_type,
                candidate_limit=candidate_limit,
                eligible_source_ids=(
                    eligible_source_ids
                ),
            )

            warnings.extend(
                channel_warnings
            )

    sources: dict[
        tuple[str, uuid.UUID],
        dict[str, Any],
    ] = {}

    for row in semantic_rows:
        payload = row["payload"]
        row_source_type = str(
            payload.get("source_type") or ""
        ).lower()
        row_source_id = _safe_uuid(
            payload.get("source_id")
        )
        if (
            row_source_type
            not in {"email", "attachment", "document"}
            or row_source_id is None
        ):
            continue
        key = _source_key(
            row_source_type,
            row_source_id,
        )
        source = sources.setdefault(
            key,
            _empty_source(
                row_source_type,
                row_source_id,
            ),
        )
        source["semantic_rank"] = min(
            row["rank"],
            source["semantic_rank"]
            if source["semantic_rank"] is not None
            else row["rank"],
        )
        source["semantic_score"] = max(
            row["score"],
            float(source["semantic_score"] or 0.0),
        )
        _merge_chunk(
            source,
            chunk_id=payload.get("chunk_id"),
            chunk_index=payload.get("chunk_index"),
            chunk_text=str(payload.get("text") or ""),
            semantic_rank=row["rank"],
            semantic_score=row["score"],
        )

    max_lexical_score = max(
        (
            float(row["score"])
            for row in lexical_rows
        ),
        default=0.0,
    )
    for row in lexical_rows:
        row_source_type = str(
            row["source_type"] or ""
        ).lower()
        row_source_id = _safe_uuid(row["source_id"])
        if (
            row_source_type
            not in {"email", "attachment", "document"}
            or row_source_id is None
        ):
            continue
        key = _source_key(
            row_source_type,
            row_source_id,
        )
        source = sources.setdefault(
            key,
            _empty_source(
                row_source_type,
                row_source_id,
            ),
        )
        source["lexical_rank"] = min(
            row["rank"],
            source["lexical_rank"]
            if source["lexical_rank"] is not None
            else row["rank"],
        )
        source["lexical_score_raw"] = max(
            float(row["score"]),
            float(
                source["lexical_score_raw"] or 0.0
            ),
        )
        _merge_chunk(
            source,
            chunk_id=row["chunk_id"],
            chunk_index=row["chunk_index"],
            chunk_text=str(row["text"] or ""),
            lexical_rank=row["rank"],
            lexical_score_raw=float(row["score"]),
        )

    _enrich_sources(sources)

    ranked: list[dict[str, Any]] = []
    quality_rejections: dict[str, int] = {}
    metadata_filtered_source_count = 0

    for source in sources.values():
        if not _source_passes_filters(
            source,
            sender=sender,
            filename=filename,
            date_from=start_date,
            date_to=end_date,
        ):
            continue

        metadata_filtered_source_count += 1

        metadata = source["metadata"]
        metadata_text = "\n".join(
            str(metadata.get(name) or "")
            for name in (
                "subject",
                "filename",
                "sender_name",
                "sender_email",
                "folder_path",
            )
        )

        chunks = list(source["chunks"].values())
        for chunk in chunks:
            lexical_normalized = (
                float(
                    chunk["lexical_score_raw"] or 0.0
                )
                / max_lexical_score
                if max_lexical_score > 0
                else 0.0
            )
            chunk_signals = rank_signals(
                query=clean_query,
                content=chunk["text"],
                metadata_text=metadata_text,
                semantic_score=chunk["semantic_score"],
                lexical_score=lexical_normalized,
                semantic_rank=chunk["semantic_rank"],
                lexical_rank=chunk["lexical_rank"],
                rrf_k=settings.hybrid_search_rrf_k,
                mode=clean_mode,
            )
            chunk["signals"] = chunk_signals

        chunks.sort(
            key=lambda item: (
                item["signals"].final,
                item["signals"].exact,
                item["signals"].coverage,
            ),
            reverse=True,
        )
        selected_chunks = chunks[:safe_snippet_count]
        if not selected_chunks:
            continue
        best_chunk = selected_chunks[0]
        source_lexical = (
            float(source["lexical_score_raw"] or 0.0)
            / max_lexical_score
            if max_lexical_score > 0
            else 0.0
        )
        source_signals = rank_signals(
            query=clean_query,
            content=best_chunk["text"],
            metadata_text=metadata_text,
            semantic_score=max(
                float(source["semantic_score"] or 0.0),
                float(
                    best_chunk["semantic_score"] or 0.0
                ),
            ),
            lexical_score=max(
                source_lexical,
                float(
                    best_chunk["lexical_score_raw"] or 0.0
                )
                / max_lexical_score
                if max_lexical_score > 0
                else 0.0,
            ),
            semantic_rank=source["semantic_rank"],
            lexical_rank=source["lexical_rank"],
            rrf_k=settings.hybrid_search_rrf_k,
            mode=clean_mode,
        )

        passes_quality, rejection_reason = _quality_gate(
            query=clean_query,
            mode=clean_mode,
            signals=source_signals,
            best_chunk_signals=best_chunk["signals"],
            min_score=safe_min_score,
        )
        if not passes_quality:
            quality_rejections[rejection_reason or "other"] = (
                quality_rejections.get(
                    rejection_reason or "other",
                    0,
                )
                + 1
            )
            continue

        matched_chunks = [
            {
                "chunk_id": chunk["chunk_id"],
                "chunk_index": chunk["chunk_index"],
                "score": chunk["signals"].final,
                "semantic_score": (
                    chunk["signals"].semantic
                ),
                "lexical_score": (
                    chunk["signals"].lexical
                ),
                "exact_match": bool(
                    chunk["signals"].exact
                ),
                "token_coverage": (
                    chunk["signals"].coverage
                ),
                "matched_text": _excerpt(
                    chunk["text"],
                    query=clean_query,
                    limit=settings.hybrid_search_snippet_chars,
                ),
            }
            for chunk in selected_chunks
        ]

        ranked.append(
            {
                "score": source_signals.final,
                "source_type": source["source_type"],
                "source_id": str(source["source_id"]),
                "source_ref": metadata["source_ref"],
                "parent_source_id": metadata.get(
                    "parent_source_id"
                ),
                "parent_source_ref": metadata.get(
                    "parent_source_ref"
                ),
                "thread_id": metadata.get("thread_id"),
                "subject": metadata.get("subject"),
                "filename": metadata.get("filename"),
                "mime_type": metadata.get("mime_type"),
                "sender_name": metadata.get(
                    "sender_name"
                ),
                "sender_email": metadata.get(
                    "sender_email"
                ),
                "date": metadata.get("date"),
                "folder_path": metadata.get(
                    "folder_path"
                ),
                "content_hash": metadata.get(
                    "content_hash"
                ),
                "match_reasons": list(
                    source_signals.reasons
                ),
                "score_components": {
                    "semantic": (
                        source_signals.semantic
                    ),
                    "lexical": (
                        source_signals.lexical
                    ),
                    "token_coverage": (
                        source_signals.coverage
                    ),
                    "exact": source_signals.exact,
                    "metadata": (
                        source_signals.metadata
                    ),
                    "anchor_coverage": (
                        source_signals.anchors
                    ),
                    "primary_anchor": (
                        source_signals.primary_anchor
                    ),
                    "rrf": source_signals.rrf,
                },
                "matched_chunks": matched_chunks,
            }
        )

    ranked.sort(
        key=lambda item: (
            item["score"],
            item["score_components"]["exact"],
            item["score_components"]["token_coverage"],
            item["date"] or "",
        ),
        reverse=True,
    )
    final_results: list[dict[str, Any]] = []
    seen_diversity_keys: set[tuple[str, str]] = set()
    collapsed_duplicates = 0
    for item in ranked:
        keys = _diversity_keys(item)
        if any(key in seen_diversity_keys for key in keys):
            collapsed_duplicates += 1
            continue
        seen_diversity_keys.update(keys)
        final_results.append(item)
        if len(final_results) >= safe_limit:
            break

    for rank, item in enumerate(
        final_results,
        start=1,
    ):
        item["rank"] = rank

    elapsed_ms = round(
        (time.perf_counter() - started) * 1000,
        2,
    )
    return {
        "query": clean_query,
        "mode": clean_mode,
        "source_type": clean_source_type,
        "identifier_query": identifier_query,
        "count": len(final_results),
        "candidate_counts": {
            "semantic_chunks": len(semantic_rows),
            "lexical_chunks": len(lexical_rows),
            "unique_sources_before_filters": len(sources),
            "unique_sources_after_metadata_filters": (
                metadata_filtered_source_count
            ),
            "unique_sources_after_quality_gate": len(ranked),
            "collapsed_thread_or_file_duplicates": (
                collapsed_duplicates
            ),
        },
        "quality_rejections": quality_rejections,
        "elapsed_ms": elapsed_ms,
        "warnings": warnings,
        "results": final_results,
    }
