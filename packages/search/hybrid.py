from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from typing import Any

from qdrant_client import models as qmodels
from sqlalchemy import select, text

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
    informative_query_tokens,
    is_identifier_query,
    normalize_query,
    primary_anchor_token,
    query_tokens,
    rank_signals,
)

FTS_INDEX_NAME = "ix_document_chunks_text_fts_simple"
VALID_MODES = {"hybrid", "semantic", "lexical"}
VALID_SOURCE_TYPES = {"all", "email", "attachment", "document"}


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


def _semantic_candidates(
    *,
    query: str,
    source_type: str,
    candidate_limit: int,
    min_score: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
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

        query_filter = None
        if source_type != "all":
            query_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="source_type",
                        match=qmodels.MatchValue(
                            value=source_type
                        ),
                    )
                ]
            )

        vector = embed_texts([query])[0]
        response = client.query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            query_filter=query_filter,
            limit=candidate_limit,
            score_threshold=(
                min_score if min_score > 0 else None
            ),
            with_payload=True,
        )
        return [
            {
                "rank": rank,
                "score": float(point.score),
                "point_id": str(point.id),
                "payload": point.payload or {},
            }
            for rank, point in enumerate(
                response.points,
                start=1,
            )
        ], warnings
    except Exception as exc:
        warnings.append(
            "Semantic search channel failed and was skipped: "
            f"{type(exc).__name__}: {exc}"
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


def _lexical_candidates(
    *,
    query: str,
    source_type: str,
    candidate_limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not _fts_index_available():
        return [], [
            "PostgreSQL hybrid-search index is missing; "
            "run `alembic upgrade head`."
        ]

    identifier = is_identifier_query(query)
    relaxed_terms: list[str] = []
    seen_terms: set[str] = set()
    for token in informative_query_tokens(query):
        for term in re.findall(
            r"[0-9A-Za-zА-Яа-яЁё]+",
            token,
        ):
            folded = term.casefold()
            if len(folded) < 2 or folded in seen_terms:
                continue
            seen_terms.add(folded)
            relaxed_terms.append(
                f"{folded}:*"
                if len(folded) >= 4
                and not folded.isdigit()
                else folded
            )
    relaxed_query = (
        " | ".join(relaxed_terms)
        or "workmindnomatch"
    )

    if identifier:
        score_sql = """
            GREATEST(
                ts_rank_cd(
                    to_tsvector(
                        'simple'::regconfig,
                        COALESCE(chunks.text, ''::text)
                    ),
                    search_query.strict_query,
                    32
                ),
                0.65 * ts_rank_cd(
                    to_tsvector(
                        'simple'::regconfig,
                        COALESCE(chunks.text, ''::text)
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
                    COALESCE(chunks.text, ''::text)
                ) @@ search_query.strict_query
                OR
                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(chunks.text, ''::text)
                ) @@ search_query.relaxed_query
            )
        """
    else:
        # For natural-language hybrid queries semantic search supplies
        # recall. The lexical channel remains strict to avoid broad OR
        # matches on common words and numbers.
        score_sql = """
            ts_rank_cd(
                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(chunks.text, ''::text)
                ),
                search_query.strict_query,
                32
            )
        """
        match_sql = """
            to_tsvector(
                'simple'::regconfig,
                COALESCE(chunks.text, ''::text)
            ) @@ search_query.strict_query
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
            {score_sql} AS lexical_score
        FROM document_chunks AS chunks
        CROSS JOIN search_query
        WHERE
            {match_sql}
            AND (
                :source_type = 'all'
                OR chunks.source_type = :source_type
            )
        ORDER BY lexical_score DESC, chunks.id
        LIMIT :candidate_limit
        """
    )
    try:
        with SessionLocal() as session:
            rows = session.execute(
                statement,
                {
                    "query": query,
                    "relaxed_query": relaxed_query,
                    "source_type": source_type,
                    "candidate_limit": candidate_limit,
                },
            ).mappings().all()
        return [
            {
                "rank": rank,
                "chunk_id": row["id"],
                "source_type": row["source_type"],
                "source_id": row["source_id"],
                "chunk_index": row["chunk_index"],
                "text": row["text"],
                "score": float(
                    row["lexical_score"] or 0.0
                ),
            }
            for rank, row in enumerate(rows, start=1)
        ], []
    except Exception as exc:
        return [], [
            "Lexical search channel failed and was skipped: "
            f"{type(exc).__name__}: {exc}"
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


def _excerpt(
    value: str,
    *,
    query: str,
    limit: int,
) -> str:
    clean = value.strip()
    if len(clean) <= limit:
        return clean

    folded = clean.casefold()
    normalized_query = normalize_query(query)
    position = folded.find(normalized_query)
    if position < 0:
        positions = [
            folded.find(token)
            for token in query_tokens(query)
            if folded.find(token) >= 0
        ]
        position = min(positions) if positions else 0

    before = min(240, max(0, limit // 4))
    start = max(0, position - before)
    end = min(len(clean), start + limit)
    if end - start < limit:
        start = max(0, end - limit)

    excerpt = clean[start:end].strip()
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
    if min_score > 0 and signals.final < min_score:
        return False, "below_min_score"

    primary_anchor = primary_anchor_token(query)
    if (
        primary_anchor is not None
        and best_chunk_signals.primary_anchor < 0.999
        and signals.metadata < 0.999
    ):
        return False, "primary_anchor_missing"

    informative_count = len(
        informative_query_tokens(query)
    )
    if is_identifier_query(query):
        if (
            signals.exact
            or best_chunk_signals.coverage >= 0.45
            or best_chunk_signals.lexical >= 0.40
        ):
            return True, None
        return False, "weak_identifier_match"

    if mode == "semantic":
        if (
            best_chunk_signals.semantic >= 0.42
            or best_chunk_signals.coverage >= 0.45
            or signals.exact
        ):
            return True, None
        return False, "weak_semantic_match"

    if informative_count >= 5:
        if (
            best_chunk_signals.coverage >= 0.34
            or (
                best_chunk_signals.semantic >= 0.55
                and best_chunk_signals.coverage >= 0.16
            )
            or signals.exact
        ):
            return True, None
        return False, "low_query_coverage"

    if informative_count >= 3:
        if (
            best_chunk_signals.coverage >= 0.34
            or best_chunk_signals.semantic >= 0.50
            or signals.exact
        ):
            return True, None
        return False, "low_query_coverage"

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

    warnings: list[str] = []
    semantic_rows: list[dict[str, Any]] = []
    lexical_rows: list[dict[str, Any]] = []

    if clean_mode in {"hybrid", "semantic"}:
        semantic_rows, channel_warnings = (
            _semantic_candidates(
                query=clean_query,
                source_type=clean_source_type,
                candidate_limit=candidate_limit,
                min_score=safe_min_score,
            )
        )
        warnings.extend(channel_warnings)

    if clean_mode in {"hybrid", "lexical"}:
        lexical_rows, channel_warnings = (
            _lexical_candidates(
                query=clean_query,
                source_type=clean_source_type,
                candidate_limit=candidate_limit,
            )
        )
        warnings.extend(channel_warnings)

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
            not in {"email", "attachment"}
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
    for source in sources.values():
        if not _source_passes_filters(
            source,
            sender=sender,
            filename=filename,
            date_from=start_date,
            date_to=end_date,
        ):
            continue

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
        "identifier_query": is_identifier_query(
            clean_query
        ),
        "count": len(final_results),
        "candidate_counts": {
            "semantic_chunks": len(semantic_rows),
            "lexical_chunks": len(lexical_rows),
            "unique_sources_before_filters": len(sources),
            "unique_sources_after_metadata_filters": (
                len(sources)
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
