from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import exists, func, or_, select, text
from qdrant_client import models as qmodels
from sqlalchemy.orm import Session, selectinload

from packages.common.config import get_settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailMessage,
    EmailRecipient,
    EmailThread,
    ExternalDocument,
    Mailbox,
    ProcessingJob,
    EmailLocation,
    HistoryImportState,
    AgentActionLog,
)
from packages.database.session import SessionLocal
from packages.processing.embeddings import (
    embed_texts,
    embedding_service_status,
)
from packages.search.hybrid import search_hybrid
from packages.search.ranking import rank_signals
from packages.search.qdrant import get_qdrant_client


class WorkMindQueryError(ValueError):
    """A user-facing query validation or lookup error."""


def _uuid(value: str, *, field_name: str = "source_id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise WorkMindQueryError(
            f"{field_name} must be a valid UUID, got: {value!r}"
        ) from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _occurred_at(message: EmailMessage) -> datetime | None:
    return message.received_at or message.sent_at


def _source_ref(message_id: uuid.UUID) -> str:
    return f"workmind://email/{message_id}"


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
                return datetime.combine(
                    parsed_date + timedelta(days=1),
                    time.min,
                    tzinfo=UTC,
                )
            return datetime.combine(parsed_date, time.min, tzinfo=UTC)

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError as exc:
        raise WorkMindQueryError(
            f"Invalid ISO date/datetime: {value!r}. "
            "Use YYYY-MM-DD or ISO 8601."
        ) from exc


def _like_pattern(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _truncate(text: str | None, limit: int) -> tuple[str, bool]:
    value = text or ""
    safe_limit = max(100, min(limit, 200_000))
    if len(value) <= safe_limit:
        return value, False
    return value[:safe_limit], True


def _message_summary(message: EmailMessage) -> dict[str, Any]:
    return {
        "source_id": str(message.id),
        "source_ref": _source_ref(message.id),
        "thread_id": str(message.thread_id) if message.thread_id else None,
        "subject": message.subject,
        "sender_name": message.sender_name,
        "sender_email": message.sender_email,
        "date": _iso(_occurred_at(message)),
        "folder_path": message.folder_path,
        "has_attachments": message.has_attachments,
        "processing_status": message.processing_status,
    }


def _recipient_dict(recipient: EmailRecipient) -> dict[str, Any]:
    return {
        "type": recipient.recipient_type,
        "display_name": recipient.display_name,
        "email": recipient.email,
    }


def _attachment_dict(attachment: Attachment) -> dict[str, Any]:
    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size_bytes": attachment.size_bytes,
        "archive_path": attachment.archive_path,
        "processing_status": attachment.processing_status,
        "content_hash": attachment.content_hash,
        "extracted_text_path": attachment.extracted_text_path,
        "extraction_method": attachment.extraction_method,
        "extracted_chars": attachment.extracted_chars,
        "page_count": attachment.page_count,
        "sheet_count": attachment.sheet_count,
        "last_error": attachment.last_error,
        "processed_at": _iso(attachment.processed_at),
        "metadata": attachment.metadata_json,
    }


def _external_document_dict(
    document: ExternalDocument,
) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "source_id": str(document.id),
        "source_ref": f"workmind://document/{document.id}",
        "filename": document.original_filename,
        "mime_type": document.mime_type,
        "size_bytes": document.size_bytes,
        "source_date": _iso(document.source_date),
        "archive_path": document.archive_path,
        "processed_text_path": document.processed_text_path,
        "processing_status": document.processing_status,
        "content_hash": document.content_hash,
        "extraction_method": document.extraction_method,
        "extracted_chars": document.extracted_chars,
        "page_count": document.page_count,
        "sheet_count": document.sheet_count,
        "slide_count": document.slide_count,
        "last_error": document.last_error,
        "metadata": document.metadata_json,
        "imported_at": _iso(document.imported_at),
        "processed_at": _iso(document.processed_at),
    }


def _location_dict(location: EmailLocation) -> dict[str, Any]:
    return {
        "store_display_name": location.store_display_name,
        "source_kind": location.source_kind,
        "folder_path": location.folder_path,
        "outlook_entry_id": location.outlook_entry_id,
        "outlook_store_id": location.outlook_store_id,
        "first_seen_at": _iso(location.first_seen_at),
        "last_seen_at": _iso(location.last_seen_at),
    }


def _load_message(
    session: Session,
    message_id: uuid.UUID,
) -> EmailMessage | None:
    return session.scalar(
        select(EmailMessage)
        .where(EmailMessage.id == message_id)
        .options(
            selectinload(EmailMessage.recipients),
            selectinload(EmailMessage.attachments),
            selectinload(EmailMessage.locations),
        )
    )



def unified_search(
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
    """Search emails and attachments with semantic and lexical fusion."""
    return search_hybrid(
        query,
        limit=limit,
        mode=mode,
        source_type=source_type,
        min_score=min_score,
        sender=sender,
        filename=filename,
        date_from=date_from,
        date_to=date_to,
        snippets_per_source=snippets_per_source,
    )


def semantic_search(
    query: str,
    *,
    limit: int = 10,
    min_score: float = 0.0,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Search indexed email chunks by meaning and return unique emails."""
    clean_query = query.strip()
    if not clean_query:
        raise WorkMindQueryError("query must not be empty")

    safe_limit = max(1, min(limit, 50))
    safe_score = max(-1.0, min(float(min_score), 1.0))
    start = _parse_datetime(date_from)
    end = _parse_datetime(date_to, end_of_day=True)

    settings = get_settings()
    client = get_qdrant_client()
    if not client.collection_exists(settings.qdrant_collection):
        return {
            "query": clean_query,
            "count": 0,
            "results": [],
            "warning": "Qdrant collection does not exist yet.",
        }

    query_vector = embed_texts([clean_query])[0]
    candidate_limit = min(max(safe_limit * 12, 100), 1000)

    response = client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        query_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="source_type",
                    match=qmodels.MatchValue(value="email"),
                )
            ]
        ),
        limit=candidate_limit,
        score_threshold=safe_score if safe_score > 0 else None,
        with_payload=True,
    )

    sender_folded = sender.strip().casefold() if sender else None
    results: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    for point in response.points:
        payload = point.payload or {}
        source_id = str(payload.get("source_id") or point.id)
        if source_id in seen_sources:
            continue

        payload_sender = str(payload.get("sender_email") or "")
        if sender_folded and sender_folded not in payload_sender.casefold():
            continue

        payload_date = _parse_datetime(
            str(payload.get("source_date") or "")
        )
        if start and (payload_date is None or payload_date < start):
            continue
        if end and (payload_date is None or payload_date >= end):
            continue

        seen_sources.add(source_id)
        results.append(
            {
                "score": round(float(point.score), 6),
                "source_id": source_id,
                "source_ref": f"workmind://email/{source_id}",
                "thread_id": payload.get("thread_id"),
                "subject": payload.get("subject"),
                "sender_name": payload.get("sender_name"),
                "sender_email": payload.get("sender_email"),
                "date": payload.get("source_date"),
                "folder_path": payload.get("folder_path"),
                "matched_text": payload.get("text"),
                "chunk_id": payload.get("chunk_id"),
                "chunk_index": payload.get("chunk_index"),
            }
        )
        if len(results) >= safe_limit:
            break

    return {
        "query": clean_query,
        "count": len(results),
        "results": results,
    }


def semantic_search_attachments(
    query: str,
    *,
    limit: int = 10,
    min_score: float = 0.0,
    filename: str | None = None,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    clean_query = query.strip()
    if not clean_query:
        raise WorkMindQueryError("query must not be empty")

    safe_limit = max(1, min(limit, 50))
    safe_score = max(-1.0, min(float(min_score), 1.0))
    start = _parse_datetime(date_from)
    end = _parse_datetime(date_to, end_of_day=True)

    settings = get_settings()
    client = get_qdrant_client()
    if not client.collection_exists(settings.qdrant_collection):
        return {
            "query": clean_query,
            "count": 0,
            "results": [],
            "warning": "Qdrant collection does not exist yet.",
        }

    query_vector = embed_texts([clean_query])[0]
    candidate_limit = min(max(safe_limit * 12, 100), 1000)
    response = client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        query_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="source_type",
                    match=qmodels.MatchValue(value="attachment"),
                )
            ]
        ),
        limit=candidate_limit,
        score_threshold=safe_score if safe_score > 0 else None,
        with_payload=True,
    )

    filename_folded = filename.strip().casefold() if filename else None
    sender_folded = sender.strip().casefold() if sender else None
    results: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    for point in response.points:
        payload = point.payload or {}
        source_id = str(payload.get("source_id") or point.id)
        if source_id in seen_sources:
            continue

        payload_filename = str(payload.get("filename") or "")
        if (
            filename_folded
            and filename_folded not in payload_filename.casefold()
        ):
            continue

        payload_sender = " ".join(
            [
                str(payload.get("sender_name") or ""),
                str(payload.get("sender_email") or ""),
            ]
        )
        if (
            sender_folded
            and sender_folded not in payload_sender.casefold()
        ):
            continue

        payload_date = _parse_datetime(
            str(payload.get("source_date") or "")
        )
        if start and (payload_date is None or payload_date < start):
            continue
        if end and (payload_date is None or payload_date >= end):
            continue

        seen_sources.add(source_id)
        results.append(
            {
                "score": round(float(point.score), 6),
                "source_id": source_id,
                "source_ref": (
                    payload.get("source_ref")
                    or f"workmind://attachment/{source_id}"
                ),
                "parent_source_id": payload.get("parent_source_id"),
                "parent_source_ref": payload.get("parent_source_ref"),
                "filename": payload.get("filename"),
                "mime_type": payload.get("mime_type"),
                "subject": payload.get("subject"),
                "sender_name": payload.get("sender_name"),
                "sender_email": payload.get("sender_email"),
                "date": payload.get("source_date"),
                "folder_path": payload.get("folder_path"),
                "matched_text": payload.get("text"),
                "chunk_id": payload.get("chunk_id"),
                "chunk_index": payload.get("chunk_index"),
            }
        )
        if len(results) >= safe_limit:
            break

    return {
        "query": clean_query,
        "count": len(results),
        "results": results,
    }


def exact_search_attachments(
    query: str,
    *,
    limit: int = 20,
    filename: str | None = None,
) -> dict[str, Any]:
    clean_query = query.strip()
    if not clean_query:
        raise WorkMindQueryError("query must not be empty")

    safe_limit = max(1, min(limit, 100))
    pattern = _like_pattern(clean_query)
    filename_pattern = (
        _like_pattern(filename.strip())
        if filename
        else None
    )

    with SessionLocal() as session:
        statement = (
            select(DocumentChunk, Attachment, EmailMessage)
            .join(
                Attachment,
                Attachment.id == DocumentChunk.source_id,
            )
            .join(
                EmailMessage,
                EmailMessage.id == Attachment.message_id,
            )
            .where(
                DocumentChunk.source_type == "attachment",
                DocumentChunk.text.ilike(pattern, escape="\\"),
            )
            .order_by(
                func.coalesce(
                    EmailMessage.received_at,
                    EmailMessage.sent_at,
                ).desc().nullslast()
            )
            .limit(safe_limit * 10)
        )
        if filename_pattern:
            statement = statement.where(
                Attachment.filename.ilike(
                    filename_pattern,
                    escape="\\",
                )
            )
        rows = session.execute(statement).all()

    results: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    for chunk, attachment, message in rows:
        if attachment.id in seen:
            continue
        seen.add(attachment.id)
        results.append(
            {
                "source_id": str(attachment.id),
                "source_ref": (
                    f"workmind://attachment/{attachment.id}"
                ),
                "parent_source_id": str(message.id),
                "parent_source_ref": _source_ref(message.id),
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "subject": message.subject,
                "sender_name": message.sender_name,
                "sender_email": message.sender_email,
                "date": _iso(_occurred_at(message)),
                "folder_path": message.folder_path,
                "matched_text": chunk.text,
                "chunk_index": chunk.chunk_index,
            }
        )
        if len(results) >= safe_limit:
            break

    return {
        "query": clean_query,
        "count": len(results),
        "results": results,
    }


def get_attachment(
    source_id: str,
    *,
    max_text_chars: int = 100_000,
) -> dict[str, Any]:
    attachment_id = _uuid(
        source_id,
        field_name="attachment_id",
    )

    with SessionLocal() as session:
        attachment = session.get(Attachment, attachment_id)
        if attachment is None:
            raise WorkMindQueryError(
                f"Attachment not found: {source_id}"
            )
        message = session.get(
            EmailMessage,
            attachment.message_id,
        )
        if message is None:
            raise WorkMindQueryError(
                "Parent email not found."
            )

        text = ""
        if attachment.extracted_text_path:
            path = Path(attachment.extracted_text_path)
            if path.exists():
                text = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
        if not text:
            text = "\n\n".join(
                session.scalars(
                    select(DocumentChunk.text)
                    .where(
                        DocumentChunk.source_type == "attachment",
                        DocumentChunk.source_id == attachment.id,
                    )
                    .order_by(DocumentChunk.chunk_index)
                )
            )

        text, truncated = _truncate(
            text,
            max_text_chars,
        )
        return {
            **_attachment_dict(attachment),
            "source_id": str(attachment.id),
            "source_ref": (
                f"workmind://attachment/{attachment.id}"
            ),
            "parent_source_id": str(message.id),
            "parent_source_ref": _source_ref(message.id),
            "subject": message.subject,
            "sender_name": message.sender_name,
            "sender_email": message.sender_email,
            "date": _iso(_occurred_at(message)),
            "folder_path": message.folder_path,
            "extracted_text": text,
            "text_truncated": truncated,
        }


def get_external_document(
    source_id: str,
    *,
    max_text_chars: int = 100_000,
) -> dict[str, Any]:
    document_id = _uuid(source_id)
    with SessionLocal() as session:
        document = session.get(ExternalDocument, document_id)
        if document is None:
            raise WorkMindQueryError(
                f"External document not found: {source_id}"
            )
        extracted_text = ""
        if document.processed_text_path:
            path = Path(document.processed_text_path)
            if path.exists():
                extracted_text = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
        if not extracted_text:
            extracted_text = "\n\n".join(
                session.scalars(
                    select(DocumentChunk.text)
                    .where(
                        DocumentChunk.source_type == "document",
                        DocumentChunk.source_id == document.id,
                    )
                    .order_by(DocumentChunk.chunk_index)
                )
            )
        extracted_text, truncated = _truncate(
            extracted_text,
            max_text_chars,
        )
        return {
            **_external_document_dict(document),
            "extracted_text": extracted_text,
            "text_truncated": truncated,
        }


def exact_search(
    query: str,
    *,
    limit: int = 20,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    subject_only: bool = False,
) -> dict[str, Any]:
    """Case-insensitive literal substring search in PostgreSQL."""
    clean_query = query.strip()
    if not clean_query:
        raise WorkMindQueryError("query must not be empty")

    safe_limit = max(1, min(limit, 100))
    pattern = _like_pattern(clean_query)
    start = _parse_datetime(date_from)
    end = _parse_datetime(date_to, end_of_day=True)

    recipient_match = exists(
        select(1).where(
            EmailRecipient.message_id == EmailMessage.id,
            or_(
                EmailRecipient.email.ilike(pattern, escape="\\"),
                EmailRecipient.display_name.ilike(pattern, escape="\\"),
            ),
        )
    )

    if subject_only:
        text_condition = EmailMessage.subject.ilike(pattern, escape="\\")
    else:
        text_condition = or_(
            EmailMessage.subject.ilike(pattern, escape="\\"),
            EmailMessage.body_text.ilike(pattern, escape="\\"),
            EmailMessage.sender_name.ilike(pattern, escape="\\"),
            EmailMessage.sender_email.ilike(pattern, escape="\\"),
            EmailMessage.internet_message_id.ilike(pattern, escape="\\"),
            recipient_match,
        )

    conditions = [text_condition]

    if sender:
        sender_pattern = _like_pattern(sender.strip())
        conditions.append(
            or_(
                EmailMessage.sender_name.ilike(
                    sender_pattern,
                    escape="\\",
                ),
                EmailMessage.sender_email.ilike(
                    sender_pattern,
                    escape="\\",
                ),
            )
        )

    occurred = func.coalesce(
        EmailMessage.received_at,
        EmailMessage.sent_at,
    )
    if start:
        conditions.append(occurred >= start)
    if end:
        conditions.append(occurred < end)

    with SessionLocal() as session:
        messages = list(
            session.scalars(
                select(EmailMessage)
                .where(*conditions)
                .order_by(occurred.desc().nullslast())
                .limit(safe_limit)
            )
        )

    return {
        "query": clean_query,
        "count": len(messages),
        "results": [_message_summary(item) for item in messages],
    }


def get_email(
    source_id: str,
    *,
    max_body_chars: int = 50_000,
    include_html: bool = False,
) -> dict[str, Any]:
    """Return a complete archived email with recipients and attachments."""
    message_id = _uuid(source_id)

    with SessionLocal() as session:
        message = _load_message(session, message_id)
        if message is None:
            raise WorkMindQueryError(
                f"Email not found: {source_id}"
            )

        body_text, body_truncated = _truncate(
            message.body_text,
            max_body_chars,
        )
        body_html = None
        html_truncated = False
        if include_html:
            body_html, html_truncated = _truncate(
                message.body_html,
                max_body_chars,
            )

        result = _message_summary(message)
        result.update(
            {
                "mailbox_id": str(message.mailbox_id),
                "outlook_entry_id": message.outlook_entry_id,
                "outlook_store_id": message.outlook_store_id,
                "internet_message_id": message.internet_message_id,
                "conversation_id": message.conversation_id,
                "sent_at": _iso(message.sent_at),
                "received_at": _iso(message.received_at),
                "body_text": body_text,
                "body_truncated": body_truncated,
                "body_html": body_html,
                "html_truncated": html_truncated,
                "recipients": [
                    _recipient_dict(item)
                    for item in message.recipients
                ],
                "attachments": [
                    _attachment_dict(item)
                    for item in message.attachments
                ],
                "locations": [
                    _location_dict(item)
                    for item in message.locations
                ],
                "raw_json_path": message.raw_json_path,
                "content_hash": message.content_hash,
                "created_at": _iso(message.created_at),
                "updated_at": _iso(message.updated_at),
            }
        )
        return result


def get_thread(
    source_id: str,
    *,
    max_body_chars_per_message: int = 20_000,
) -> dict[str, Any]:
    """Return the full conversation containing the selected email."""
    message_id = _uuid(source_id)

    with SessionLocal() as session:
        selected = _load_message(session, message_id)
        if selected is None:
            raise WorkMindQueryError(
                f"Email not found: {source_id}"
            )

        if selected.thread_id is None:
            message_ids = [selected.id]
            thread = None
        else:
            thread = session.get(EmailThread, selected.thread_id)
            occurred = func.coalesce(
                EmailMessage.received_at,
                EmailMessage.sent_at,
            )
            message_ids = list(
                session.scalars(
                    select(EmailMessage.id)
                    .where(EmailMessage.thread_id == selected.thread_id)
                    .order_by(occurred.asc().nullsfirst())
                )
            )

        messages: list[dict[str, Any]] = []
        for item_id in message_ids:
            item = _load_message(session, item_id)
            if item is None:
                continue
            body, truncated = _truncate(
                item.body_text,
                max_body_chars_per_message,
            )
            value = _message_summary(item)
            value.update(
                {
                    "recipients": [
                        _recipient_dict(recipient)
                        for recipient in item.recipients
                    ],
                    "body_text": body,
                    "body_truncated": truncated,
                    "attachments": [
                        _attachment_dict(attachment)
                        for attachment in item.attachments
                    ],
                    "locations": [
                        _location_dict(location)
                        for location in item.locations
                    ],
                }
            )
            messages.append(value)

        return {
            "thread_id": (
                str(selected.thread_id)
                if selected.thread_id
                else None
            ),
            "conversation_id": (
                thread.conversation_id if thread else selected.conversation_id
            ),
            "subject_normalized": (
                thread.subject_normalized if thread else None
            ),
            "message_count": len(messages),
            "first_message_at": (
                _iso(thread.first_message_at) if thread else None
            ),
            "last_message_at": (
                _iso(thread.last_message_at) if thread else None
            ),
            "selected_source_id": source_id,
            "messages": messages,
        }


def list_attachments(source_id: str) -> dict[str, Any]:
    """Return attachment metadata for one archived email."""
    message_id = _uuid(source_id)

    with SessionLocal() as session:
        message = _load_message(session, message_id)
        if message is None:
            raise WorkMindQueryError(
                f"Email not found: {source_id}"
            )

        return {
            "source_id": source_id,
            "source_ref": _source_ref(message.id),
            "subject": message.subject,
            "count": len(message.attachments),
            "attachments": [
                _attachment_dict(item)
                for item in message.attachments
            ],
            "note": (
                "Use search_attachments, search_attachments_exact, or "
                "get_attachment_by_id for extracted attachment content."
            ),
        }


def _source_evidence_kind(
    *,
    source_type: str,
    subject: str | None,
    sender_email: str | None,
) -> str:
    if source_type == "attachment":
        return "attachment_excerpt"
    if source_type == "document":
        return "document_excerpt"
    folded_subject = (subject or "").casefold()
    folded_sender = (sender_email or "").casefold()
    if (
        "bitrix24" in folded_subject
        or folded_sender.startswith("b24@")
    ):
        return "notification_excerpt"
    return "email_excerpt"


def _evidence_candidates(
    *,
    source_type: str,
    source_id: uuid.UUID,
    query: str | None,
    limit: int,
    excerpt_chars: int,
    subject: str | None,
    sender_email: str | None,
) -> list[dict[str, Any]]:
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    safe_limit = max(1, min(int(limit), 20))
    safe_excerpt = max(200, min(int(excerpt_chars), 5000))
    with SessionLocal() as session:
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.source_type == source_type,
                    DocumentChunk.source_id == source_id,
                )
                .order_by(DocumentChunk.chunk_index)
            )
        )

    ranked: list[tuple[float, DocumentChunk, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        signals = rank_signals(
            query=clean_query,
            content=chunk.text,
            metadata_text=(subject or ""),
            semantic_score=None,
            lexical_score=None,
            semantic_rank=None,
            lexical_rank=index,
            mode="lexical",
        )
        if signals.coverage <= 0 and not signals.exact:
            continue
        ranked.append((signals.final, chunk, signals))

    ranked.sort(
        key=lambda item: (
            item[0],
            item[2].exact,
            item[2].coverage,
        ),
        reverse=True,
    )
    kind = _source_evidence_kind(
        source_type=source_type,
        subject=subject,
        sender_email=sender_email,
    )
    result: list[dict[str, Any]] = []
    for score, chunk, signals in ranked[:safe_limit]:
        excerpt, truncated = _truncate(chunk.text, safe_excerpt)
        result.append(
            {
                "chunk_id": str(chunk.id),
                "chunk_index": chunk.chunk_index,
                "source_ref": (
                    f"workmind://{source_type}/{source_id}"
                ),
                "source_kind": kind,
                "excerpt": excerpt,
                "excerpt_truncated": truncated,
                "score": score,
                "exact_match": bool(signals.exact),
                "token_coverage": signals.coverage,
                "classification_required": [
                    "direct_statement",
                    "indirect_report",
                    "notification",
                    "inference",
                    "conflicting",
                ],
            }
        )
    return result



# ---------------------------------------------------------------------
# Compact MCP context projection
# ---------------------------------------------------------------------

_CONTEXT_CONTRACT_VERSION = 2

_CONTEXT_SOURCE_BODY_CAP = 6_000
_CONTEXT_SOURCE_TEXT_CAP = 12_000
_CONTEXT_THREAD_MESSAGE_CAP = 8
_CONTEXT_THREAD_BODY_CAP = 2_000
_CONTEXT_ATTACHMENT_METADATA_CAP = 20
_CONTEXT_RECIPIENT_CAP = 20


def _normalize_context_signature(
    value: str | None,
) -> str:
    """
    Normalize text only for exact context deduplication.

    This does not modify archived source text.
    """

    text_value = (
        value
        or ""
    )

    text_value = (
        text_value
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
    )

    text_value = re.sub(
        r"(?m)^\s*>+\s?",
        "",
        text_value,
    )

    return re.sub(
        r"\s+",
        " ",
        text_value,
    ).strip().casefold()


def _compact_attachment_metadata(
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    Keep only agent-useful attachment metadata.

    Local paths, hashes, processing errors and arbitrary metadata
    stay available through the raw attachment tools.
    """

    return {
        "attachment_id": item.get(
            "attachment_id"
        ),
        "filename": item.get(
            "filename"
        ),
        "mime_type": item.get(
            "mime_type"
        ),
        "size_bytes": item.get(
            "size_bytes"
        ),
        "processing_status": item.get(
            "processing_status"
        ),
        "extracted_chars": item.get(
            "extracted_chars"
        ),
        "page_count": item.get(
            "page_count"
        ),
        "sheet_count": item.get(
            "sheet_count"
        ),
        "processed_at": item.get(
            "processed_at"
        ),
    }


def _compact_attachment_list(
    values: list[dict[str, Any]],
    *,
    exclude_attachment_id: str | None = None,
) -> tuple[
    list[dict[str, Any]],
    int,
]:
    result: list[
        dict[str, Any]
    ] = []

    omitted = 0

    for item in values:
        if (
            exclude_attachment_id
            and str(
                item.get(
                    "attachment_id"
                )
                or ""
            )
            == exclude_attachment_id
        ):
            continue

        if (
            len(
                result
            )
            >= _CONTEXT_ATTACHMENT_METADATA_CAP
        ):
            omitted += 1
            continue

        result.append(
            _compact_attachment_metadata(
                item
            )
        )

    return (
        result,
        omitted,
    )


def _compact_email_payload(
    value: dict[str, Any],
    *,
    body_limit: int,
) -> dict[str, Any]:
    body_text, truncated = _truncate(
        value.get(
            "body_text"
        ),
        min(
            int(
                body_limit
            ),
            _CONTEXT_SOURCE_BODY_CAP,
        ),
    )

    attachments, attachments_omitted = (
        _compact_attachment_list(
            list(
                value.get(
                    "attachments"
                )
                or []
            )
        )
    )

    recipients = list(
        value.get(
            "recipients"
        )
        or []
    )

    safe_recipients = (
        recipients[
            :_CONTEXT_RECIPIENT_CAP
        ]
    )

    return {
        "source_id": value.get(
            "source_id"
        ),
        "source_ref": value.get(
            "source_ref"
        ),
        "thread_id": value.get(
            "thread_id"
        ),
        "conversation_id": value.get(
            "conversation_id"
        ),
        "subject": value.get(
            "subject"
        ),
        "sender_name": value.get(
            "sender_name"
        ),
        "sender_email": value.get(
            "sender_email"
        ),
        "date": value.get(
            "date"
        ),
        "sent_at": value.get(
            "sent_at"
        ),
        "received_at": value.get(
            "received_at"
        ),
        "folder_path": value.get(
            "folder_path"
        ),
        "has_attachments": value.get(
            "has_attachments"
        ),
        "processing_status": value.get(
            "processing_status"
        ),
        "body_text": body_text,
        "body_truncated": (
            bool(
                value.get(
                    "body_truncated"
                )
            )
            or truncated
        ),
        "recipients": safe_recipients,
        "recipients_omitted": max(
            0,
            len(
                recipients
            )
            - len(
                safe_recipients
            ),
        ),
        "attachment_count": len(
            list(
                value.get(
                    "attachments"
                )
                or []
            )
        ),
        "attachments": attachments,
        "attachments_omitted": attachments_omitted,
    }


def _compact_attachment_payload(
    value: dict[str, Any],
    *,
    text_limit: int,
) -> dict[str, Any]:
    extracted_text, truncated = _truncate(
        value.get(
            "extracted_text"
        ),
        min(
            int(
                text_limit
            ),
            _CONTEXT_SOURCE_TEXT_CAP,
        ),
    )

    result = (
        _compact_attachment_metadata(
            value
        )
    )

    result.update(
        {
            "source_id": value.get(
                "source_id"
            ),
            "source_ref": value.get(
                "source_ref"
            ),
            "parent_source_id": value.get(
                "parent_source_id"
            ),
            "parent_source_ref": value.get(
                "parent_source_ref"
            ),
            "subject": value.get(
                "subject"
            ),
            "sender_name": value.get(
                "sender_name"
            ),
            "sender_email": value.get(
                "sender_email"
            ),
            "date": value.get(
                "date"
            ),
            "folder_path": value.get(
                "folder_path"
            ),
            "extracted_text": extracted_text,
            "text_truncated": (
                bool(
                    value.get(
                        "text_truncated"
                    )
                )
                or truncated
            ),
        }
    )

    return result


def _compact_document_payload(
    value: dict[str, Any],
    *,
    text_limit: int,
) -> dict[str, Any]:
    extracted_text, truncated = _truncate(
        value.get(
            "extracted_text"
        ),
        min(
            int(
                text_limit
            ),
            _CONTEXT_SOURCE_TEXT_CAP,
        ),
    )

    return {
        "document_id": value.get(
            "document_id"
        ),
        "source_id": value.get(
            "source_id"
        ),
        "source_ref": value.get(
            "source_ref"
        ),
        "filename": value.get(
            "filename"
        ),
        "mime_type": value.get(
            "mime_type"
        ),
        "size_bytes": value.get(
            "size_bytes"
        ),
        "source_date": value.get(
            "source_date"
        ),
        "processing_status": value.get(
            "processing_status"
        ),
        "extraction_method": value.get(
            "extraction_method"
        ),
        "extracted_chars": value.get(
            "extracted_chars"
        ),
        "page_count": value.get(
            "page_count"
        ),
        "sheet_count": value.get(
            "sheet_count"
        ),
        "slide_count": value.get(
            "slide_count"
        ),
        "processed_at": value.get(
            "processed_at"
        ),
        "extracted_text": extracted_text,
        "text_truncated": (
            bool(
                value.get(
                    "text_truncated"
                )
            )
            or truncated
        ),
    }


def _select_context_thread_positions(
    total: int,
    selected_index: int,
    *,
    limit: int,
) -> list[int]:
    """
    Select a deterministic bounded thread window.

    Priority:
    - selected message and its local neighborhood;
    - newest messages for latest status/context.

    Returned positions are chronological.
    """

    if total <= 0:
        return []

    safe_limit = max(
        1,
        min(
            int(
                limit
            ),
            _CONTEXT_THREAD_MESSAGE_CAP,
        ),
    )

    selected_index = max(
        0,
        min(
            int(
                selected_index
            ),
            total - 1,
        ),
    )

    priorities: list[int] = []

    for delta in (
        -2,
        -1,
        0,
        1,
        2,
    ):
        candidate = (
            selected_index
            + delta
        )

        if (
            0
            <= candidate
            < total
        ):
            priorities.append(
                candidate
            )

    for candidate in range(
        max(
            0,
            total - 3,
        ),
        total,
    ):
        priorities.append(
            candidate
        )

    # Fill remaining slots around the selected message
    # before considering arbitrary distant history.
    radius = 3

    while (
        len(
            set(
                priorities
            )
        )
        < safe_limit
        and (
            selected_index - radius >= 0
            or selected_index + radius < total
        )
    ):
        left = (
            selected_index
            - radius
        )

        right = (
            selected_index
            + radius
        )

        if left >= 0:
            priorities.append(
                left
            )

        if right < total:
            priorities.append(
                right
            )

        radius += 1

    selected: list[int] = []

    for candidate in priorities:
        if (
            candidate
            not in selected
        ):
            selected.append(
                candidate
            )

        if (
            len(
                selected
            )
            >= safe_limit
        ):
            break

    return sorted(
        selected
    )


def _compact_context_chunks(
    chunks: list[DocumentChunk],
    *,
    source_ref: str,
    char_limit: int,
    seen_signatures: set[str],
    emit_text: bool = True,
) -> dict[str, Any]:
    """
    Build one compact text projection from indexed chunks.

    Exact normalized chunks already seen earlier in the bounded
    conversation are omitted. Every emitted fragment retains its
    original chunk_id provenance.
    """

    safe_limit = max(
        0,
        min(
            int(
                char_limit
            ),
            _CONTEXT_THREAD_BODY_CAP,
        ),
    )

    pieces: list[str] = []
    chunk_refs: list[
        dict[str, Any]
    ] = []

    used_chars = 0
    duplicate_chunks = 0
    duplicate_chars = 0
    truncated = False

    for chunk in chunks:
        raw_text = (
            chunk.text
            or ""
        )

        signature = (
            _normalize_context_signature(
                raw_text
            )
        )

        if not signature:
            continue

        if (
            signature
            in seen_signatures
        ):
            duplicate_chunks += 1
            duplicate_chars += len(
                raw_text
            )
            truncated = True
            continue

        # A chunk may participate in exact deduplication only
        # after its FULL text has actually been emitted.
        #
        # Do not seed the seen set from:
        # - the selected thread message whose body is intentionally
        #   omitted here;
        # - a chunk skipped because the output budget is exhausted;
        # - a partially emitted chunk.
        #
        # Otherwise later context could disappear merely because an
        # earlier copy existed in source data but was never visible
        # to the MCP/LLM consumer.
        if not emit_text:
            continue

        if safe_limit <= 0:
            truncated = True
            continue

        separator_chars = (
            2
            if pieces
            else 0
        )

        remaining = (
            safe_limit
            - used_chars
            - separator_chars
        )

        if remaining <= 0:
            truncated = True
            continue

        excerpt = (
            raw_text[
                :remaining
            ]
        )

        fully_emitted = (
            len(
                excerpt
            )
            == len(
                raw_text
            )
        )

        if not fully_emitted:
            truncated = True

        if not excerpt:
            continue

        pieces.append(
            excerpt
        )

        used_chars += (
            separator_chars
            + len(
                excerpt
            )
        )

        chunk_refs.append(
            {
                "chunk_id": str(
                    chunk.id
                ),
                "chunk_index": (
                    chunk.chunk_index
                ),
                "source_ref": (
                    source_ref
                ),
            }
        )

        if fully_emitted:
            seen_signatures.add(
                signature
            )

    return {
        "body_text": "\n\n".join(
            pieces
        ),
        "body_truncated": truncated,
        "chunk_refs": chunk_refs,
        "duplicate_chunks_omitted": duplicate_chunks,
        "duplicate_chars_omitted": duplicate_chars,
    }


def _compact_context_raw_body(
    raw_body: str | None,
    *,
    char_limit: int,
    seen_signatures: set[str],
    emit_text: bool = True,
) -> dict[str, Any]:
    """
    Compact a raw email body when indexed chunks are unavailable.

    The exact-dedup seen set may contain only text that was fully
    emitted to the consumer. Hidden, skipped, or partially emitted
    raw bodies must not suppress a later complete copy.
    """

    value = (
        raw_body
        or ""
    )

    signature = (
        _normalize_context_signature(
            value
        )
    )

    if (
        signature
        and signature
        in seen_signatures
    ):
        return {
            "body_text": "",
            "body_truncated": bool(
                value
            ),
            "chunk_refs": [],
            "duplicate_chunks_omitted": 1,
            "duplicate_chars_omitted": len(
                value
            ),
        }

    if not emit_text:
        return {
            "body_text": "",
            "body_truncated": bool(
                value
            ),
            "chunk_refs": [],
            "duplicate_chunks_omitted": 0,
            "duplicate_chars_omitted": 0,
        }

    body_text, body_truncated = (
        _truncate(
            value,
            char_limit,
        )
    )

    fully_emitted = (
        len(
            body_text
        )
        == len(
            value
        )
    )

    if (
        signature
        and fully_emitted
    ):
        seen_signatures.add(
            signature
        )

    return {
        "body_text": body_text,
        "body_truncated": (
            body_truncated
        ),
        "chunk_refs": [],
        "duplicate_chunks_omitted": 0,
        "duplicate_chars_omitted": 0,
    }


def _compact_thread_context(
    source_id: uuid.UUID,
    *,
    max_messages: int,
    max_body_chars_per_message: int,
) -> dict[str, Any] | None:
    """
    Load a bounded thread projection directly from PostgreSQL.

    It intentionally does not call get_thread(), because get_thread()
    loads every full message in conversations that may contain
    hundreds of emails.
    """

    safe_message_limit = max(
        1,
        min(
            int(
                max_messages
            ),
            _CONTEXT_THREAD_MESSAGE_CAP,
        ),
    )

    safe_body_limit = max(
        300,
        min(
            int(
                max_body_chars_per_message
            ),
            _CONTEXT_THREAD_BODY_CAP,
        ),
    )

    with SessionLocal() as session:
        selected = session.get(
            EmailMessage,
            source_id,
        )

        if selected is None:
            raise WorkMindQueryError(
                f"Email not found: {source_id}"
            )

        if selected.thread_id is None:
            ordered_message_ids = [
                selected.id
            ]

            thread = None

        else:
            thread = session.get(
                EmailThread,
                selected.thread_id,
            )

            occurred = func.coalesce(
                EmailMessage.received_at,
                EmailMessage.sent_at,
            )

            # Scan only IDs for the whole thread.
            #
            # Some Outlook conversations contain hundreds of
            # messages and very large quoted-history bodies.
            # Loading full EmailMessage ORM rows here would defeat
            # the compact-context memory/DB-I/O boundary even though
            # only a maximum of eight messages are returned.
            ordered_message_ids = list(
                session.scalars(
                    select(
                        EmailMessage.id
                    )
                    .where(
                        EmailMessage.thread_id
                        == selected.thread_id
                    )
                    .order_by(
                        occurred
                        .asc()
                        .nullsfirst(),
                        EmailMessage.id.asc(),
                    )
                )
            )

        try:
            selected_index = (
                ordered_message_ids.index(
                    selected.id
                )
            )
        except ValueError:
            raise WorkMindQueryError(
                "Selected email is missing from its thread."
            )

        positions = (
            _select_context_thread_positions(
                len(
                    ordered_message_ids
                ),
                selected_index,
                limit=safe_message_limit,
            )
        )

        chosen_ids = [
            ordered_message_ids[
                position
            ]
            for position in positions
        ]

        # Only now load complete EmailMessage rows, and only for the
        # bounded set actually selected for the compact projection.
        chosen_rows = list(
            session.scalars(
                select(
                    EmailMessage
                )
                .where(
                    EmailMessage.id.in_(
                        chosen_ids
                    )
                )
            )
        )

        chosen_by_id = {
            item.id: item
            for item in chosen_rows
        }

        chosen = [
            chosen_by_id[
                item_id
            ]
            for item_id in chosen_ids
            if item_id
            in chosen_by_id
        ]

        chunk_rows = list(
            session.scalars(
                select(
                    DocumentChunk
                )
                .where(
                    DocumentChunk.source_type
                    == "email",
                    DocumentChunk.source_id.in_(
                        chosen_ids
                    ),
                    DocumentChunk.embedding_status
                    == "indexed",
                )
                .order_by(
                    DocumentChunk.source_id,
                    DocumentChunk.chunk_index,
                )
            )
        )

        chunks_by_source: dict[
            uuid.UUID,
            list[DocumentChunk],
        ] = {}

        for chunk in chunk_rows:
            chunks_by_source.setdefault(
                chunk.source_id,
                [],
            ).append(
                chunk
            )

        seen_signatures: set[str] = set()

        messages: list[
            dict[str, Any]
        ] = []

        duplicate_chunks = 0
        duplicate_chars = 0

        for item in chosen:
            is_selected = (
                item.id
                == selected.id
            )

            source_ref = (
                _source_ref(
                    item.id
                )
            )

            chunks = (
                chunks_by_source.get(
                    item.id,
                    [],
                )
            )

            if chunks:
                projection = (
                    _compact_context_chunks(
                        chunks,
                        source_ref=source_ref,
                        char_limit=(
                            safe_body_limit
                        ),
                        seen_signatures=(
                            seen_signatures
                        ),
                        emit_text=(
                            not is_selected
                        ),
                    )
                )

            else:
                projection = (
                    _compact_context_raw_body(
                        item.body_text,
                        char_limit=(
                            safe_body_limit
                        ),
                        seen_signatures=(
                            seen_signatures
                        ),
                        emit_text=(
                            not is_selected
                        ),
                    )
                )

            duplicate_chunks += int(
                projection[
                    "duplicate_chunks_omitted"
                ]
            )

            duplicate_chars += int(
                projection[
                    "duplicate_chars_omitted"
                ]
            )

            value = (
                _message_summary(
                    item
                )
            )

            value.update(
                {
                    "selected_source": (
                        is_selected
                    ),
                    "body_text": (
                        projection[
                            "body_text"
                        ]
                    ),
                    "body_truncated": (
                        projection[
                            "body_truncated"
                        ]
                    ),
                    "body_omitted_reason": (
                        "selected_source"
                        if is_selected
                        else None
                    ),
                    "chunk_refs": (
                        projection[
                            "chunk_refs"
                        ]
                    ),
                }
            )

            messages.append(
                value
            )

        total_messages = len(
            ordered_message_ids
        )

        return {
            "thread_id": (
                str(
                    selected.thread_id
                )
                if selected.thread_id
                else None
            ),
            "conversation_id": (
                thread.conversation_id
                if thread
                else selected.conversation_id
            ),
            "subject_normalized": (
                thread.subject_normalized
                if thread
                else None
            ),
            "message_count": total_messages,
            "returned_message_count": len(
                messages
            ),
            "messages_omitted": max(
                0,
                total_messages
                - len(
                    messages
                ),
            ),
            "first_message_at": (
                _iso(
                    thread.first_message_at
                )
                if thread
                else _iso(
                    _occurred_at(
                        selected
                    )
                )
            ),
            "last_message_at": (
                _iso(
                    thread.last_message_at
                )
                if thread
                else _iso(
                    _occurred_at(
                        selected
                    )
                )
            ),
            "selected_source_id": str(
                source_id
            ),
            "duplicate_chunks_omitted": (
                duplicate_chunks
            ),
            "duplicate_chars_omitted": (
                duplicate_chars
            ),
            "messages": messages,
        }


def _dedupe_context_evidence(
    values: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Remove exact duplicate excerpts without changing provenance
    of the first retained evidence candidate.
    """

    result: list[
        dict[str, Any]
    ] = []

    seen: set[str] = set()

    for item in values:
        signature = (
            _normalize_context_signature(
                str(
                    item.get(
                        "excerpt"
                    )
                    or ""
                )
            )
        )

        if (
            signature
            and signature in seen
        ):
            continue

        if signature:
            seen.add(
                signature
            )

        result.append(
            item
        )

    return result


def get_workmind_context(
    source_ref_or_id: str,
    *,
    source_type: str | None = None,
    include_thread: bool = True,
    max_body_chars: int = 6_000,
    max_attachment_text_chars: int = 12_000,
    max_body_chars_per_thread_message: int = 2_000,
    max_thread_messages: int = 8,
    query: str | None = None,
    evidence_limit: int = 5,
    evidence_excerpt_chars: int = 1600,
) -> dict[str, Any]:
    """
    Return a compact, provenance-preserving context projection.

    This tool is intentionally bounded for MCP/LLM use.
    Use get_email_by_id, get_email_thread, get_attachment_by_id,
    or get_document_by_id when full raw source content is required.
    """

    raw = (
        source_ref_or_id.strip()
    )

    if not raw:
        raise WorkMindQueryError(
            "source_ref_or_id must not be empty"
        )

    detected_type = (
        source_type.strip().lower()
        if source_type
        else None
    )

    if raw.startswith(
        "workmind://email/"
    ):
        detected_type = "email"
        raw = raw.removeprefix(
            "workmind://email/"
        )

    elif raw.startswith(
        "workmind://attachment/"
    ):
        detected_type = "attachment"
        raw = raw.removeprefix(
            "workmind://attachment/"
        )

    elif raw.startswith(
        "workmind://document/"
    ):
        detected_type = "document"
        raw = raw.removeprefix(
            "workmind://document/"
        )

    source_id = _uuid(
        raw
    )

    if detected_type not in {
        None,
        "email",
        "attachment",
        "document",
    }:
        raise WorkMindQueryError(
            "source_type must be email, attachment, or document"
        )

    if detected_type is None:
        with SessionLocal() as session:
            if (
                session.get(
                    ExternalDocument,
                    source_id,
                )
                is not None
            ):
                detected_type = "document"

            elif (
                session.get(
                    Attachment,
                    source_id,
                )
                is not None
            ):
                detected_type = "attachment"

            elif (
                session.get(
                    EmailMessage,
                    source_id,
                )
                is not None
            ):
                detected_type = "email"

            else:
                raise WorkMindQueryError(
                    f"Source not found: {source_id}"
                )

    safe_body_limit = max(
        500,
        min(
            int(
                max_body_chars
            ),
            _CONTEXT_SOURCE_BODY_CAP,
        ),
    )

    safe_text_limit = max(
        1_000,
        min(
            int(
                max_attachment_text_chars
            ),
            _CONTEXT_SOURCE_TEXT_CAP,
        ),
    )

    safe_thread_body_limit = max(
        300,
        min(
            int(
                max_body_chars_per_thread_message
            ),
            _CONTEXT_THREAD_BODY_CAP,
        ),
    )

    safe_thread_messages = max(
        1,
        min(
            int(
                max_thread_messages
            ),
            _CONTEXT_THREAD_MESSAGE_CAP,
        ),
    )

    if detected_type == "email":
        full_email = get_email(
            str(
                source_id
            ),
            max_body_chars=(
                safe_body_limit
            ),
            include_html=False,
        )

        email = (
            _compact_email_payload(
                full_email,
                body_limit=(
                    safe_body_limit
                ),
            )
        )

        thread = (
            _compact_thread_context(
                source_id,
                max_messages=(
                    safe_thread_messages
                ),
                max_body_chars_per_message=(
                    safe_thread_body_limit
                ),
            )
            if include_thread
            else None
        )

        evidence = (
            _dedupe_context_evidence(
                _evidence_candidates(
                    source_type="email",
                    source_id=source_id,
                    query=query,
                    limit=evidence_limit,
                    excerpt_chars=(
                        evidence_excerpt_chars
                    ),
                    subject=email.get(
                        "subject"
                    ),
                    sender_email=email.get(
                        "sender_email"
                    ),
                )
            )
        )

        sibling_attachments = list(
            email.get(
                "attachments"
            )
            or []
        )

        return {
            "context_contract_version": (
                _CONTEXT_CONTRACT_VERSION
            ),
            "context_mode": "compact",
            "source_type": "email",
            "source_id": str(
                source_id
            ),
            "source_ref": (
                f"workmind://email/{source_id}"
            ),
            "email": email,
            "attachment": None,
            "document": None,
            "sibling_attachments": (
                sibling_attachments
            ),
            "thread": thread,
            "evidence_candidates": (
                evidence
            ),
            "context_limits": {
                "source_body_chars": (
                    safe_body_limit
                ),
                "source_text_chars": (
                    safe_text_limit
                ),
                "thread_messages": (
                    safe_thread_messages
                ),
                "thread_body_chars_per_message": (
                    safe_thread_body_limit
                ),
                "evidence_limit": max(
                    1,
                    min(
                        int(
                            evidence_limit
                        ),
                        20,
                    ),
                ),
                "evidence_excerpt_chars": max(
                    200,
                    min(
                        int(
                            evidence_excerpt_chars
                        ),
                        5_000,
                    ),
                ),
            },
            "evidence_note": (
                "Use source_ref + chunk_id from evidence_candidates "
                "or thread chunk_refs for provenance. "
                "The selected source body is not repeated inside thread."
            ),
        }

    if detected_type == "document":
        full_document = (
            get_external_document(
                str(
                    source_id
                ),
                max_text_chars=(
                    safe_text_limit
                ),
            )
        )

        document = (
            _compact_document_payload(
                full_document,
                text_limit=(
                    safe_text_limit
                ),
            )
        )

        evidence = (
            _dedupe_context_evidence(
                _evidence_candidates(
                    source_type="document",
                    source_id=source_id,
                    query=query,
                    limit=evidence_limit,
                    excerpt_chars=(
                        evidence_excerpt_chars
                    ),
                    subject=document.get(
                        "filename"
                    ),
                    sender_email=None,
                )
            )
        )

        return {
            "context_contract_version": (
                _CONTEXT_CONTRACT_VERSION
            ),
            "context_mode": "compact",
            "source_type": "document",
            "source_id": str(
                source_id
            ),
            "source_ref": (
                f"workmind://document/{source_id}"
            ),
            "document": document,
            "attachment": None,
            "email": None,
            "sibling_attachments": [],
            "thread": None,
            "evidence_candidates": (
                evidence
            ),
            "context_limits": {
                "source_text_chars": (
                    safe_text_limit
                ),
                "evidence_limit": max(
                    1,
                    min(
                        int(
                            evidence_limit
                        ),
                        20,
                    ),
                ),
            },
            "evidence_note": (
                "Use source_ref + chunk_id from evidence_candidates "
                "for provenance. Use get_document_by_id for larger "
                "raw extracted text."
            ),
        }

    full_attachment = (
        get_attachment(
            str(
                source_id
            ),
            max_text_chars=(
                safe_text_limit
            ),
        )
    )

    attachment = (
        _compact_attachment_payload(
            full_attachment,
            text_limit=(
                safe_text_limit
            ),
        )
    )

    parent_id = str(
        full_attachment[
            "parent_source_id"
        ]
    )

    full_email = get_email(
        parent_id,
        max_body_chars=(
            safe_body_limit
        ),
        include_html=False,
    )

    email = (
        _compact_email_payload(
            full_email,
            body_limit=(
                safe_body_limit
            ),
        )
    )

    parent_uuid = _uuid(
        parent_id
    )

    thread = (
        _compact_thread_context(
            parent_uuid,
            max_messages=(
                safe_thread_messages
            ),
            max_body_chars_per_message=(
                safe_thread_body_limit
            ),
        )
        if include_thread
        else None
    )

    sibling_attachments, sibling_omitted = (
        _compact_attachment_list(
            list(
                full_email.get(
                    "attachments"
                )
                or []
            ),
            exclude_attachment_id=(
                str(
                    source_id
                )
            ),
        )
    )

    evidence = (
        _dedupe_context_evidence(
            _evidence_candidates(
                source_type="attachment",
                source_id=source_id,
                query=query,
                limit=evidence_limit,
                excerpt_chars=(
                    evidence_excerpt_chars
                ),
                subject=email.get(
                    "subject"
                ),
                sender_email=email.get(
                    "sender_email"
                ),
            )
        )
    )

    return {
        "context_contract_version": (
            _CONTEXT_CONTRACT_VERSION
        ),
        "context_mode": "compact",
        "source_type": "attachment",
        "source_id": str(
            source_id
        ),
        "source_ref": (
            f"workmind://attachment/{source_id}"
        ),
        "attachment": attachment,
        "email": email,
        "document": None,
        "sibling_attachments": (
            sibling_attachments
        ),
        "sibling_attachments_omitted": (
            sibling_omitted
        ),
        "thread": thread,
        "evidence_candidates": (
            evidence
        ),
        "context_limits": {
            "source_body_chars": (
                safe_body_limit
            ),
            "source_text_chars": (
                safe_text_limit
            ),
            "thread_messages": (
                safe_thread_messages
            ),
            "thread_body_chars_per_message": (
                safe_thread_body_limit
            ),
            "evidence_limit": max(
                1,
                min(
                    int(
                        evidence_limit
                    ),
                    20,
                ),
            ),
        },
        "evidence_note": (
            "Use source_ref + chunk_id from evidence_candidates "
            "or thread chunk_refs for provenance. "
            "Use get_attachment_by_id/get_email_by_id/get_email_thread "
            "when larger raw content is required."
        ),
    }


def system_status() -> dict[str, Any]:
    """Return PostgreSQL, job queue, chunk index, Qdrant, and sync status."""
    settings = get_settings()

    last_sync: dict[str, Any]
    if settings.sync_status_file.exists():
        try:
            loaded = json.loads(
                settings.sync_status_file.read_text(
                    encoding="utf-8"
                )
            )
            last_sync = (
                loaded
                if isinstance(loaded, dict)
                else {"status": "invalid"}
            )
        except (OSError, json.JSONDecodeError) as exc:
            last_sync = {
                "status": "invalid",
                "error": str(exc),
            }
    else:
        last_sync = {"status": "never_run"}

    if settings.history_status_file.exists():
        try:
            loaded_history = json.loads(
                settings.history_status_file.read_text(
                    encoding="utf-8"
                )
            )
            history_status = (
                loaded_history
                if isinstance(loaded_history, dict)
                else {"status": "invalid"}
            )
        except (OSError, json.JSONDecodeError) as exc:
            history_status = {
                "status": "invalid",
                "error": str(exc),
            }
    else:
        history_status = {"status": "never_run"}

    if settings.document_import_status_file.exists():
        try:
            loaded_documents = json.loads(
                settings.document_import_status_file.read_text(
                    encoding="utf-8"
                )
            )
            document_import_status = (
                loaded_documents
                if isinstance(loaded_documents, dict)
                else {"status": "invalid"}
            )
        except (OSError, json.JSONDecodeError) as exc:
            document_import_status = {
                "status": "invalid",
                "error": str(exc),
            }
    else:
        document_import_status = {"status": "never_run"}

    with SessionLocal() as session:
        database_counts = {
            "mailboxes": session.scalar(
                select(func.count()).select_from(Mailbox)
            ),
            "threads": session.scalar(
                select(func.count()).select_from(EmailThread)
            ),
            "messages": session.scalar(
                select(func.count()).select_from(EmailMessage)
            ),
            "message_locations": session.scalar(
                select(func.count()).select_from(EmailLocation)
            ),
            "history_folders": session.scalar(
                select(func.count()).select_from(HistoryImportState)
            ),
            "attachments": session.scalar(
                select(func.count()).select_from(Attachment)
            ),
            "external_documents": session.scalar(
                select(func.count()).select_from(ExternalDocument)
            ),
            "attachment_chunks": session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.source_type == "attachment")
            ),
            "document_chunks": session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.source_type == "document")
            ),
            "chunks": session.scalar(
                select(func.count()).select_from(DocumentChunk)
            ),
            "agent_action_logs": session.scalar(
                select(func.count()).select_from(AgentActionLog)
            ),
        }

        job_rows = session.execute(
            select(
                ProcessingJob.status,
                func.count(),
            ).group_by(ProcessingJob.status)
        ).all()
        jobs = {
            str(status): int(count)
            for status, count in job_rows
        }

        chunk_rows = session.execute(
            select(
                DocumentChunk.embedding_status,
                func.count(),
            ).group_by(DocumentChunk.embedding_status)
        ).all()
        chunk_statuses = {
            str(status): int(count)
            for status, count in chunk_rows
        }

        attachment_rows = session.execute(
            select(
                Attachment.processing_status,
                func.count(),
            ).group_by(Attachment.processing_status)
        ).all()
        attachment_statuses = {
            str(status): int(count)
            for status, count in attachment_rows
        }

        document_rows = session.execute(
            select(
                ExternalDocument.processing_status,
                func.count(),
            ).group_by(ExternalDocument.processing_status)
        ).all()
        document_statuses = {
            str(status): int(count)
            for status, count in document_rows
        }

        fts_index_ready = bool(
            session.scalar(
                text(
                    "SELECT to_regclass("
                    "'public.ix_document_chunks_text_fts_simple'"
                    ") IS NOT NULL"
                )
            )
        )

        active_mailboxes = list(
            session.execute(
                select(
                    Mailbox.name,
                    Mailbox.is_active,
                    func.count(EmailMessage.id),
                )
                .outerjoin(
                    EmailMessage,
                    EmailMessage.mailbox_id == Mailbox.id,
                )
                .group_by(Mailbox.id, Mailbox.name, Mailbox.is_active)
                .order_by(func.count(EmailMessage.id).desc())
            ).all()
        )

    qdrant_status: dict[str, Any]
    try:
        qdrant = get_qdrant_client()
        exists_collection = qdrant.collection_exists(
            settings.qdrant_collection
        )
        if exists_collection:
            info = qdrant.get_collection(
                settings.qdrant_collection
            )
            raw_status = getattr(info, "status", None)
            qdrant_status = {
                "reachable": True,
                "collection": settings.qdrant_collection,
                "collection_exists": True,
                "status": getattr(raw_status, "value", str(raw_status)),
                "points_count": getattr(info, "points_count", None),
                "vectors_count": getattr(info, "vectors_count", None),
                "indexed_vectors_count": getattr(
                    info,
                    "indexed_vectors_count",
                    None,
                ),
            }
        else:
            qdrant_status = {
                "reachable": True,
                "collection": settings.qdrant_collection,
                "collection_exists": False,
            }
    except Exception as exc:
        qdrant_status = {
            "reachable": False,
            "collection": settings.qdrant_collection,
            "error": str(exc),
        }

    return {
        "read_only": True,
        "database": database_counts,
        "processing_jobs": jobs,
        "chunk_statuses": chunk_statuses,
        "attachment_statuses": attachment_statuses,
        "document_statuses": document_statuses,
        "mailboxes": [
            {
                "name": name,
                "is_active": is_active,
                "messages": int(message_count),
            }
            for name, is_active, message_count in active_mailboxes
        ],
        "hybrid_search": {
            "fts_index_ready": fts_index_ready,
            "fts_index": "ix_document_chunks_text_fts_simple",
            "unified_tool": "search_workmind",
        },
        "qdrant": qdrant_status,
        "embedding_service": embedding_service_status(
            timeout_seconds=1.5
        ),
        "last_mail_sync": last_sync,
        "history_import": history_status,
        "document_import": document_import_status,
        "configuration": {
            "database_host": "localhost",
            "qdrant_url": settings.qdrant_url,
            "embedding_model": settings.embedding_model,
            "workmind_home": str(settings.workmind_home),
            "document_import_dir": str(
                settings.document_import_dir
            ),
        },
    }
