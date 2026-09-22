from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from packages.common.config import Settings, get_settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailMessage,
    ExternalDocument,
    KnowledgeExtractionJob,
)
from packages.database.session import SessionLocal
from packages.knowledge.identity_resolution import (
    duplicate_risk_diagnostics,
    ontology_diagnostics,
    resolve_identity_candidates,
)
from packages.knowledge.service import import_knowledge_bundle

SUPPORTED_SOURCE_TYPES = {"email", "attachment", "document"}
ACTIVE_JOB_STATUSES = {"pending", "leased", "validated"}
TERMINAL_JOB_STATUSES = {"completed", "skipped", "superseded", "error"}
PACKET_CONTRACT_VERSION = "wm-ke-v2"
QUALITY_FAILURE_EVENTS = {
    "apply_failure",
    "encoding_or_scope_validation_failure",
    "fingerprint_mismatch",
    "incomplete_identity_context",
    "invalid_json",
    "lease_mismatch",
    "packet_build_failure",
    "preflight_failure",
    "semantic_validation_failure",
    "source_fingerprint_mismatch",
}
EXCLUSIVE_PREDICATES = {
    "status",
    "current_status",
    "owner",
    "assignee",
    "deadline",
    "decision",
}
ENTITY_ONTOLOGY = {
    "system": (
        "A concrete IT system, application, module, integration, software "
        "component, or other technical object with stable identity."
    ),
    "process": (
        "A repeatable business or operational process or procedure. Do not "
        "choose system merely because the source uses the word processing."
    ),
    "task": "A specific piece of work or assignment with a bounded objective.",
    "project": "A larger initiative combining multiple tasks or deliverables.",
    "incident": "A specific failure, defect, outage, accident, or operational problem.",
    "decision": "A decision that was actually made, not a proposal or discussion.",
    "person": "A specific natural person with stable identity.",
    "organization": "A specific company, department, team, or legal organization.",
    "document": "A specific durable document or formally identified record.",
    "other": "A stable durable entity that does not fit another defined type.",
}


class KnowledgeExtractionError(ValueError):
    """Raised for invalid extraction queue operations."""


def _now() -> datetime:
    return datetime.now(UTC)


def _quality_history(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    history = raw.get("quality_history")
    if not isinstance(history, list):
        return []
    return [dict(item) for item in history if isinstance(item, dict)]


def _append_quality_event(
    history: list[dict[str, Any]],
    *,
    attempt: int,
    event_type: str,
    details: str,
    limit: int,
) -> list[dict[str, Any]]:
    event_key = f"{attempt}:{event_type}"
    if any(item.get("event_key") == event_key for item in history):
        return history
    updated = [
        *history,
        {
            "event_key": event_key,
            "attempt": int(attempt),
            "event_type": event_type,
            "occurred_at": _now().isoformat(),
            "details": details[:2000],
        },
    ]
    return updated[-max(1, int(limit)) :]


def _quality_failure_count(history: list[dict[str, Any]]) -> int:
    return len(
        {
            int(item.get("attempt") or 0)
            for item in history
            if item.get("event_type") in QUALITY_FAILURE_EVENTS
        }
    )


def _quality_failure_total(raw: dict[str, Any] | None) -> int:
    raw = raw if isinstance(raw, dict) else {}
    base = int(raw.get("quality_history_base_failures") or 0)
    if "quality_history" not in raw:
        base = max(
            base,
            int((raw.get("quality") or {}).get("validation_failures") or 0),
        )
    return base + _quality_failure_count(_quality_history(raw))


def _quality_attempt(job: KnowledgeExtractionJob) -> int:
    validation = job.validation_json if isinstance(job.validation_json, dict) else {}
    return int(validation.get("quality_attempt_offset") or 0) + int(job.attempts)


def _validation_state(
    previous: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
    *,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    previous = previous if isinstance(previous, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    preserved = {
        key: previous[key]
        for key in (
            "bundle_sha256",
            "candidate_resolution",
            "duplicate_risk",
            "identity_snapshot",
            "ontology_diagnostics",
            "quality",
            "quality_attempt_offset",
        )
        if key in previous and key not in payload
    }
    merged = {**preserved, **payload}
    merged["quality_history"] = (
        history if history is not None else _quality_history(previous)
    )
    base_failures = int(previous.get("quality_history_base_failures") or 0)
    if "quality_history" not in previous:
        base_failures = max(
            base_failures,
            int(
                (previous.get("quality") or {}).get("validation_failures")
                or 0
            ),
        )
    merged["quality_history_base_failures"] = base_failures
    quality = dict(merged.get("quality") or {})
    quality["validation_failures"] = base_failures + _quality_failure_count(
        merged["quality_history"]
    )
    merged["quality"] = quality
    return merged


def _record_quality_event(
    session: Session,
    job: KnowledgeExtractionJob,
    *,
    event_type: str,
    details: str,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    history = _append_quality_event(
        _quality_history(job.validation_json),
        attempt=_quality_attempt(job),
        event_type=event_type,
        details=details,
        limit=settings.knowledge_extraction_quality_history_limit,
    )
    job.validation_json = _validation_state(
        job.validation_json,
        history=history,
    )


def _source_ref(source_type: str, source_id: uuid.UUID) -> str:
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise KnowledgeExtractionError(
            f"Unsupported extraction source_type: {source_type!r}"
        )
    return f"workmind://{source_type}/{source_id}"


def _uuid(value: str | uuid.UUID, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise KnowledgeExtractionError(f"Invalid {field}: {value!r}") from exc


def _source_row(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> EmailMessage | Attachment | ExternalDocument | None:
    if source_type == "email":
        return session.get(EmailMessage, source_id)
    if source_type == "attachment":
        return session.get(Attachment, source_id)
    if source_type == "document":
        return session.get(ExternalDocument, source_id)
    raise KnowledgeExtractionError(
        f"Unsupported extraction source_type: {source_type!r}"
    )


def _source_hash(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> str | None:
    row = _source_row(session, source_type, source_id)
    if row is None:
        return None
    value = getattr(row, "content_hash", None)
    if value:
        return str(value)
    hashes = list(
        session.scalars(
            select(DocumentChunk.content_hash)
            .where(
                DocumentChunk.source_type == source_type,
                DocumentChunk.source_id == source_id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
    )
    if not hashes:
        return None
    return hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()


def _source_date(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> datetime | None:
    if source_type == "email":
        message = session.get(EmailMessage, source_id)
        return (message.received_at or message.sent_at) if message else None
    if source_type == "attachment":
        attachment = session.get(Attachment, source_id)
        if attachment is None:
            return None
        message = session.get(EmailMessage, attachment.message_id)
        return (message.received_at or message.sent_at) if message else None
    document = session.get(ExternalDocument, source_id)
    return document.source_date if document else None


def _source_chunk_stats(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> tuple[int, int]:
    count, chars = session.execute(
        select(
            func.count(DocumentChunk.id),
            func.coalesce(func.sum(func.length(DocumentChunk.text)), 0),
        ).where(
            DocumentChunk.source_type == source_type,
            DocumentChunk.source_id == source_id,
            DocumentChunk.embedding_status == "indexed",
        )
    ).one()
    return int(count or 0), int(chars or 0)


def enqueue_indexed_source(
    session: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    settings: Settings | None = None,
    priority: int = 0,
    reason: str = "indexed",
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.knowledge_extraction_enabled:
        return {"queued": False, "reason": "disabled"}
    if source_type not in SUPPORTED_SOURCE_TYPES:
        return {"queued": False, "reason": "unsupported_source_type"}

    source_hash = _source_hash(session, source_type, source_id)
    if not source_hash:
        return {"queued": False, "reason": "missing_source_hash"}
    chunk_count, chars = _source_chunk_stats(session, source_type, source_id)
    if chunk_count == 0:
        return {"queued": False, "reason": "no_indexed_chunks"}
    if chars < settings.knowledge_extraction_min_chars:
        return {
            "queued": False,
            "reason": "below_min_chars",
            "chars": chars,
        }

    existing = session.scalar(
        select(KnowledgeExtractionJob).where(
            KnowledgeExtractionJob.source_type == source_type,
            KnowledgeExtractionJob.source_id == source_id,
            KnowledgeExtractionJob.source_content_hash == source_hash,
        ).order_by(KnowledgeExtractionJob.created_at.desc()).limit(1)
    )
    if existing is not None:
        return {
            "queued": False,
            "reason": "already_queued",
            "job_id": str(existing.id),
            "status": existing.status,
        }

    session.execute(
        update(KnowledgeExtractionJob)
        .where(
            KnowledgeExtractionJob.source_type == source_type,
            KnowledgeExtractionJob.source_id == source_id,
            KnowledgeExtractionJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .values(
            status="superseded",
            lease_token=None,
            leased_at=None,
            lease_expires_at=None,
            last_error="Superseded by a newer source content hash.",
            completed_at=_now(),
        )
    )

    job = KnowledgeExtractionJob(
        source_type=source_type,
        source_id=source_id,
        source_content_hash=source_hash,
        extractor_version=settings.knowledge_extraction_version,
        status="pending",
        priority=int(priority),
        attempts=0,
        bundle_json={},
        validation_json={},
        apply_json={},
        last_error=None,
    )
    session.add(job)
    session.flush()
    return {
        "queued": True,
        "job_id": str(job.id),
        "source_ref": _source_ref(source_type, source_id),
        "source_content_hash": source_hash,
        "chunk_count": chunk_count,
        "chars": chars,
        "reason": reason,
    }


def try_enqueue_indexed_source(
    session: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    settings: Settings | None = None,
    priority: int = 0,
    reason: str = "indexed",
) -> dict[str, Any]:
    """Queue knowledge extraction without risking the core indexing transaction."""
    try:
        with session.begin_nested():
            return enqueue_indexed_source(
                session,
                source_type=source_type,
                source_id=source_id,
                settings=settings,
                priority=priority,
                reason=reason,
            )
    except Exception as exc:
        return {
            "queued": False,
            "reason": "queue_error",
            "error": f"{type(exc).__name__}: {exc}"[:2000],
        }


def release_expired_leases(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    current = now or _now()
    jobs = list(
        session.scalars(
            select(KnowledgeExtractionJob)
            .where(
                KnowledgeExtractionJob.status == "leased",
                KnowledgeExtractionJob.lease_expires_at.is_not(None),
                KnowledgeExtractionJob.lease_expires_at <= current,
            )
            .with_for_update(skip_locked=True)
        )
    )
    settings = get_settings()
    for job in jobs:
        exhausted = job.attempts >= settings.knowledge_extraction_max_attempts
        job.status = "error" if exhausted else "pending"
        job.lease_token = None
        job.leased_at = None
        job.lease_expires_at = None
        job.last_error = (
            "Extraction lease expired after maximum attempts."
            if exhausted
            else "Previous extraction lease expired and was released."
        )
        if exhausted:
            job.completed_at = current
    return len(jobs)


def claim_extraction_jobs(
    *,
    limit: int = 3,
    lease_minutes: int | None = None,
) -> list[KnowledgeExtractionJob]:
    settings = get_settings()
    safe_limit = max(1, min(int(limit), 20))
    scan_limit = max(
        safe_limit,
        min(safe_limit + 32, 100),
    )
    lease_for = int(
        lease_minutes
        if lease_minutes is not None
        else settings.knowledge_extraction_lease_minutes
    )
    lease_for = max(5, min(lease_for, 1440))
    now = _now()

    with SessionLocal() as session:
        release_expired_leases(session, now=now)
        jobs = list(
            session.scalars(
                select(KnowledgeExtractionJob)
                .where(
                    KnowledgeExtractionJob.status == "pending",
                    KnowledgeExtractionJob.attempts
                    < settings.knowledge_extraction_max_attempts,
                    KnowledgeExtractionJob.available_at <= now,
                )
                .order_by(
                    KnowledgeExtractionJob.priority.desc(),
                    KnowledgeExtractionJob.created_at,
                )
                .with_for_update(skip_locked=True)
                .limit(scan_limit)
            )
        )
        leased_jobs: list[
            KnowledgeExtractionJob
        ] = []

        for job in jobs:
            if (
                len(leased_jobs)
                >= safe_limit
            ):
                break

            try:
                current_hash = _source_hash(
                    session,
                    job.source_type,
                    job.source_id,
                )
            except KnowledgeExtractionError:
                current_hash = None

            if (
                current_hash
                != job.source_content_hash
            ):
                job.status = "superseded"
                job.lease_token = None
                job.leased_at = None
                job.lease_expires_at = None
                job.completed_at = now
                job.last_error = (
                    "Source content changed or "
                    "disappeared before extraction "
                    "lease; job superseded."
                )
                continue
            job.status = "leased"
            job.attempts += 1
            leased_jobs.append(job)
            job.lease_token = uuid.uuid4()
            job.leased_at = now
            job.lease_expires_at = now + timedelta(minutes=lease_for)
        session.commit()
        return leased_jobs


def _message_meta(message: EmailMessage) -> dict[str, Any]:
    occurred_at = message.received_at or message.sent_at
    return {
        "source_ref": _source_ref("email", message.id),
        "source_type": "email",
        "source_id": str(message.id),
        "source_date": occurred_at.isoformat() if occurred_at else None,
        "subject": message.subject,
        "sender_name": message.sender_name,
        "sender_email": message.sender_email,
        "thread_id": str(message.thread_id) if message.thread_id else None,
        "folder_path": message.folder_path,
    }


def _source_metadata(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> dict[str, Any]:
    if source_type == "email":
        message = session.get(EmailMessage, source_id)
        if message is None:
            raise KnowledgeExtractionError("Email source disappeared")
        return _message_meta(message)
    if source_type == "attachment":
        attachment = session.get(Attachment, source_id)
        if attachment is None:
            raise KnowledgeExtractionError("Attachment source disappeared")
        message = session.get(EmailMessage, attachment.message_id)
        occurred_at = (
            (message.received_at or message.sent_at)
            if message
            else None
        )
        return {
            "source_ref": _source_ref("attachment", attachment.id),
            "source_type": "attachment",
            "source_id": str(attachment.id),
            "source_date": occurred_at.isoformat() if occurred_at else None,
            "filename": attachment.filename,
            "content_hash": attachment.content_hash,
            "size_bytes": attachment.size_bytes,
            "mime_type": attachment.mime_type,
            "parent_source_ref": (
                _source_ref("email", message.id) if message else None
            ),
            "subject": message.subject if message else None,
            "sender_name": message.sender_name if message else None,
            "thread_id": (
                str(message.thread_id)
                if message and message.thread_id
                else None
            ),
        }
    document = session.get(ExternalDocument, source_id)
    if document is None:
        raise KnowledgeExtractionError("Document source disappeared")
    return {
        "source_ref": _source_ref("document", document.id),
        "source_type": "document",
        "source_id": str(document.id),
        "source_date": (
            document.source_date.isoformat()
            if document.source_date
            else None
        ),
        "filename": document.original_filename,
        "content_hash": document.content_hash,
        "size_bytes": document.size_bytes,
        "mime_type": document.mime_type,
    }


def _thread_messages(
    session: Session,
    message: EmailMessage,
    *,
    limit: int,
) -> list[EmailMessage]:
    if message.thread_id is None:
        return [message]
    target_date = message.received_at or message.sent_at or message.created_at
    statement = select(EmailMessage).where(
        EmailMessage.thread_id == message.thread_id
    )
    if target_date is not None:
        statement = statement.where(
            func.coalesce(
                EmailMessage.received_at,
                EmailMessage.sent_at,
                EmailMessage.created_at,
            ) <= target_date
        )
    rows = list(
        session.scalars(
            statement
            .order_by(
                func.coalesce(
                    EmailMessage.received_at,
                    EmailMessage.sent_at,
                    EmailMessage.created_at,
                ).desc()
            )
            .limit(limit)
        )
    )
    rows.reverse()
    if not any(item.id == message.id for item in rows):
        rows.append(message)
    return rows


def _context_source_ids(
    session: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    settings: Settings,
) -> list[tuple[str, uuid.UUID]]:
    values: list[tuple[str, uuid.UUID]] = [(source_type, source_id)]
    parent: EmailMessage | None = None
    if source_type == "email":
        parent = session.get(EmailMessage, source_id)
    elif source_type == "attachment":
        attachment = session.get(Attachment, source_id)
        if attachment is not None:
            parent = session.get(EmailMessage, attachment.message_id)

    if parent is not None:
        for message in _thread_messages(
            session,
            parent,
            limit=settings.knowledge_extraction_thread_messages,
        ):
            pair = ("email", message.id)
            if pair not in values:
                values.append(pair)
    return values


def _allowed_evidence_from_chunks(
    chunks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build evidence scope from packet chunks, never identity context."""
    return [
        {
            "source_ref": str(item["source_ref"]),
            "chunk_id": str(item["chunk_id"]),
        }
        for item in chunks
    ]


def _allowed_source_metadata_evidence(
    session: Session,
    context_ids: list[tuple[str, uuid.UUID]],
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Return bounded, WorkMind-verified metadata for binary/source identity evidence."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(meta: dict[str, Any]) -> None:
        if len(rows) >= limit:
            return
        source_ref = str(meta["source_ref"])
        if source_ref in seen:
            return
        fields = (
            ("filename", "content_hash", "size_bytes", "mime_type", "parent_source_ref")
            if meta.get("source_type") == "attachment"
            else ("filename", "content_hash", "size_bytes", "mime_type", "source_date")
        )
        allowed = {
            key: meta.get(key)
            for key in fields
            if key in meta and meta.get(key) is not None
        }
        rows.append(
            {
                "source_ref": source_ref,
                "source_type": meta["source_type"],
                "metadata": allowed,
            }
        )
        seen.add(source_ref)

    for source_type, source_id in context_ids:
        if len(rows) >= limit:
            break
        if source_type == "attachment":
            add(_source_metadata(session, source_type, source_id))
            continue
        if source_type == "document":
            add(_source_metadata(session, source_type, source_id))
            continue
        if source_type != "email":
            continue
        attachments = list(
            session.scalars(
                select(Attachment)
                .where(Attachment.message_id == source_id)
                .order_by(Attachment.created_at, Attachment.id)
                .limit(max(0, limit - len(rows)))
            )
        )
        for attachment in attachments:
            add(_source_metadata(session, "attachment", attachment.id))
            if len(rows) >= limit:
                break
    return rows


_PACKET_CHUNK_QUERY_BATCH_SIZE = 256


def _iter_indexed_source_chunks(
    session: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    batch_size: int = _PACKET_CHUNK_QUERY_BATCH_SIZE,
) -> Iterator[Any]:
    """Yield indexed source chunks in bounded keyset-paginated batches."""

    safe_batch_size = max(
        1,
        min(
            int(
                batch_size
            ),
            _PACKET_CHUNK_QUERY_BATCH_SIZE,
        ),
    )

    last_chunk_index: int | None = None

    while True:
        statement = (
            select(
                DocumentChunk.id.label(
                    "chunk_id"
                ),
                DocumentChunk.chunk_index.label(
                    "chunk_index"
                ),
                DocumentChunk.text.label(
                    "text"
                ),
            )
            .where(
                DocumentChunk.source_type
                == source_type,
                DocumentChunk.source_id
                == source_id,
                DocumentChunk.embedding_status
                == "indexed",
            )
        )

        if last_chunk_index is not None:
            statement = (
                statement.where(
                    DocumentChunk.chunk_index
                    > last_chunk_index
                )
            )

        rows = (
            session.execute(
                statement
                .order_by(
                    DocumentChunk.chunk_index
                )
                .limit(
                    safe_batch_size
                )
            )
            .all()
        )

        if not rows:
            return

        for row in rows:
            yield row

        last_chunk_index = int(
            rows[
                -1
            ].chunk_index
        )

        if (
            len(
                rows
            )
            < safe_batch_size
        ):
            return


def _append_source_chunks(
    rows: Iterable[Any],
    *,
    chunks: list[dict[str, Any]],
    current_chars: int,
    max_chunks: int,
    max_context_chars: int,
    source_ref: str,
    source_type: str,
    source_id: uuid.UUID,
    source_date: str | None,
) -> int:
    """Append bounded evidence chunks with raw-exact same-source dedup.

    Deduplication is intentionally scoped to one source. Identical text from
    another source remains independently available as evidence because its
    source/date provenance may be semantically meaningful.

    Only a fully emitted chunk seeds duplicate suppression. This preserves the
    invariant that text cannot disappear merely because an earlier occurrence
    existed in storage but was not fully visible to the extractor.
    """

    chars = int(
        current_chars
    )

    seen_texts: set[str] = set()

    row_iterator = iter(
        rows
    )

    while (
        len(
            chunks
        )
        < max_chunks
        and chars
        < max_context_chars
    ):
        try:
            row = next(
                row_iterator
            )
        except StopIteration:
            break

        remaining = (
            max_context_chars
            - chars
        )

        raw_text = (
            row.text
            or ""
        )

        if raw_text in seen_texts:
            continue

        emitted_text = (
            raw_text[
                :remaining
            ]
        )

        chunks.append(
            {
                "chunk_id": str(
                    row.chunk_id
                ),
                "source_ref": (
                    source_ref
                ),
                "source_type": (
                    source_type
                ),
                "source_id": str(
                    source_id
                ),
                "source_date": (
                    source_date
                ),
                "chunk_index": int(
                    row.chunk_index
                ),
                "text": (
                    emitted_text
                ),
            }
        )

        chars += len(
            emitted_text
        )

        fully_emitted = (
            len(
                emitted_text
            )
            == len(
                raw_text
            )
        )

        if fully_emitted:
            seen_texts.add(
                raw_text
            )

        if not fully_emitted:
            break

    return chars


def build_extraction_packet(
    job_id: str | uuid.UUID,
) -> dict[str, Any]:
    settings = get_settings()
    parsed_job_id = _uuid(job_id, "job_id")
    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(f"Extraction job not found: {job_id}")
        current_hash = _source_hash(
            session,
            job.source_type,
            job.source_id,
        )
        if current_hash != job.source_content_hash:
            raise KnowledgeExtractionError(
                "Extraction job source content changed; requeue the source."
            )

        target = _source_metadata(session, job.source_type, job.source_id)
        context_ids = _context_source_ids(
            session,
            source_type=job.source_type,
            source_id=job.source_id,
            settings=settings,
        )
        chunks: list[dict[str, Any]] = []
        chars = 0

        for context_type, context_id in context_ids:
            meta = _source_metadata(
                session,
                context_type,
                context_id,
            )

            chars = _append_source_chunks(
                _iter_indexed_source_chunks(
                    session,
                    source_type=(
                        context_type
                    ),
                    source_id=(
                        context_id
                    ),
                ),
                chunks=chunks,
                current_chars=chars,
                max_chunks=(
                    settings
                    .knowledge_extraction_max_chunks
                ),
                max_context_chars=(
                    settings
                    .knowledge_extraction_max_context_chars
                ),
                source_ref=str(
                    meta[
                        "source_ref"
                    ]
                ),
                source_type=(
                    context_type
                ),
                source_id=(
                    context_id
                ),
                source_date=(
                    meta.get(
                        "source_date"
                    )
                ),
            )

            if (
                len(
                    chunks
                )
                >= settings.knowledge_extraction_max_chunks
                or chars
                >= settings.knowledge_extraction_max_context_chars
            ):
                break

        source_metadata_evidence = _allowed_source_metadata_evidence(
            session,
            context_ids,
        )
        metadata_identity_texts = [
            str(row.get("metadata", {}).get("filename") or "")
            for row in source_metadata_evidence
            if row.get("metadata", {}).get("filename")
        ]

        stored_validation = job.validation_json or {}
        history = _quality_history(stored_validation)
        identity_snapshot = stored_validation.get("identity_snapshot") or {}
        current_lease_token = str(job.lease_token) if job.lease_token else None
        if (
            identity_snapshot.get("lease_token") == current_lease_token
            and isinstance(identity_snapshot.get("candidates"), list)
            and isinstance(identity_snapshot.get("diagnostics"), dict)
        ):
            identity_candidates = identity_snapshot["candidates"]
            identity_diagnostics = identity_snapshot["diagnostics"]
        else:
            identity_resolution = resolve_identity_candidates(
                session,
                [item["text"] for item in chunks] + metadata_identity_texts,
                settings=settings,
            )
            identity_candidates = identity_resolution["candidates"]
            identity_diagnostics = identity_resolution["diagnostics"]
            job.validation_json = {
                **stored_validation,
                "candidate_resolution": identity_diagnostics,
                "identity_snapshot": {
                    "lease_token": current_lease_token,
                    "candidates": identity_candidates,
                    "diagnostics": identity_diagnostics,
                },
            }
            session.commit()
        allowed = _allowed_evidence_from_chunks(chunks)
        identity_blocking_errors = list(
            identity_diagnostics.get("blocking_errors") or []
        )
        return {
            "packet_contract_version": PACKET_CONTRACT_VERSION,
            "job": {
                "job_id": str(job.id),
                "lease_token": str(job.lease_token) if job.lease_token else None,
                "status": job.status,
                "attempts": job.attempts,
                "source_content_hash": job.source_content_hash,
                "extractor_version": job.extractor_version,
                "runtime_packet_contract_version": PACKET_CONTRACT_VERSION,
                "extraction_blocked": bool(identity_blocking_errors),
                "lease_expires_at": (
                    job.lease_expires_at.isoformat()
                    if job.lease_expires_at
                    else None
                ),
            },
            "target": target,
            "chunks": chunks,
            "allowed_evidence": allowed,
            "allowed_source_metadata_evidence": source_metadata_evidence,
            "identity_candidates": identity_candidates,
            # Compatibility alias; both fields come from the same resolution.
            "existing_knowledge_candidates": identity_candidates,
            "identity_resolution": identity_diagnostics,
            "blocking_diagnostics": identity_blocking_errors,
            "quality_history": {
                "events": history,
                "validation_failures": _quality_failure_total(stored_validation),
            },
            "entity_ontology": ENTITY_ONTOLOGY,
            "instructions": [
                (
                    "Extract durable work knowledge only: tasks, projects, "
                    "incidents, decisions, ownership, deadlines, statuses, "
                    "systems, organizations, and real events."
                ),
                (
                    "Do not extract greetings, signatures, routine "
                    "acknowledgements, quoted boilerplate, or facts not "
                    "supported by the supplied chunks."
                ),
                (
                    "Every claim, relation, and event must cite at least one "
                    "exact allowed source_ref + chunk_id pair."
                ),
                (
                    "A new entity may use identity_evidence with "
                    "evidence_kind=source_metadata, but only from "
                    "allowed_source_metadata_evidence. Source metadata proves "
                    "technical identity only; it must not replace chunk evidence "
                    "for functional claims, relations, or events."
                ),
                (
                    "Use direct_statement or notification for explicit source "
                    "facts; use inference only when unavoidable and lower "
                    "confidence accordingly."
                ),
                (
                    "identity_candidates are bounded identity and "
                    "deduplication context. They are not evidence for newly "
                    "asserted facts."
                ),
                (
                    "Every newly asserted fact still requires an exact "
                    "source_ref + chunk_id pair from allowed_evidence in this "
                    "target packet."
                ),
                (
                    "First attempt to reuse an identity_candidate. When "
                    "reusing one, copy that exact entity_id into the matching "
                    "entities[] entry."
                ),
                (
                    "Prefer an identity candidate's stable technical "
                    "canonical_name over a new paraphrased entity name."
                ),
                (
                    "A generic descriptive phrase must not automatically "
                    "become a new system entity."
                ),
                (
                    "If the source describes a process rule but identifies no "
                    "stable technical object, process may be more accurate "
                    "than system."
                ),
                (
                    "Do not create an entity only to attach a claim when the "
                    "source does not establish a durable entity identity."
                ),
                (
                    "For status-like facts, set observed_at to the source "
                    "observation time. Set valid_from only when the source "
                    "supports the effective date."
                ),
                (
                    "For exclusive predicates such as current_status, owner, "
                    "assignee, deadline, status, and decision: if the new fact "
                    "changes the current value, set supersedes_claim_id to the "
                    "conflicting active claim exposed in identity_candidates."
                ),
                (
                    "When supersedes_claim_id is used, WorkMind will close the "
                    "previous claim at the new fact's valid_from or observed_at "
                    "and mark it superseded."
                ),
                (
                    "Before creating an event, compare recent_events. If the "
                    "same real-world event already exists, reuse its event_id "
                    "and copy its immutable entity/event_type/title/occurred_at "
                    "values exactly."
                ),
                (
                    "Do not fabricate claim_id, claim_key, event_id, entity "
                    "IDs, or supersession IDs. Reuse IDs only when they appear "
                    "in identity_candidates."
                ),
                (
                    "Return one JSON object with entities, claims, relations, "
                    "and events arrays and no prose outside JSON."
                ),
                (
                    "If job.extraction_blocked is true, do not propose a bundle. "
                    "Release the job for retry after the reported identity-context "
                    "anomaly is resolved."
                ),
            ],
        }


def get_extraction_batch(
    *,
    limit: int = 3,
    lease_minutes: int | None = None,
) -> dict[str, Any]:
    jobs = claim_extraction_jobs(limit=limit, lease_minutes=lease_minutes)
    packets: list[dict[str, Any]] = []
    for job in jobs:
        try:
            packets.append(build_extraction_packet(job.id))
        except Exception as exc:
            with SessionLocal() as session:
                row = session.get(KnowledgeExtractionJob, job.id)
                if row is not None:
                    _record_quality_event(
                        session,
                        row,
                        event_type="packet_build_failure",
                        details=f"{type(exc).__name__}: {exc}",
                    )
                    row.status = "error"
                    row.last_error = f"Packet build failed: {type(exc).__name__}: {exc}"
                    row.lease_token = None
                    row.leased_at = None
                    row.lease_expires_at = None
                    row.completed_at = _now()
                    session.commit()
    return {
        "count": len(packets),
        "jobs": packets,
    }


def _bundle_evidence_pairs(bundle: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for section in ("claims", "relations", "events"):
        values = bundle.get(section, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list):
                continue
            for row in evidence:
                if not isinstance(row, dict):
                    continue
                source_ref = str(row.get("source_ref") or "").strip()
                chunk_id = str(row.get("chunk_id") or "").strip()
                if source_ref and chunk_id:
                    pairs.append((source_ref, chunk_id))
    return pairs


def _bundle_identity_metadata_evidence(
    bundle: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    values = bundle.get("entities", [])
    if not isinstance(values, list):
        return rows
    for entity in values:
        if not isinstance(entity, dict):
            continue
        evidence = entity.get("identity_evidence", [])
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict):
                continue
            source_ref = str(item.get("source_ref") or "").strip()
            metadata = item.get("metadata")
            if source_ref and isinstance(metadata, dict):
                rows.append((source_ref, metadata))
    return rows


def _check_evidence_scope(
    packet: dict[str, Any],
    bundle: dict[str, Any],
) -> list[str]:
    allowed = {
        (str(row["source_ref"]), str(row["chunk_id"]))
        for row in packet["allowed_evidence"]
    }
    allowed_metadata = {
        str(row["source_ref"]): dict(row.get("metadata") or {})
        for row in packet.get("allowed_source_metadata_evidence", [])
    }
    errors: list[str] = []
    for pair in _bundle_evidence_pairs(bundle):
        if pair not in allowed:
            errors.append(
                "Evidence outside extraction packet: "
                f"source_ref={pair[0]} chunk_id={pair[1]}"
            )
    for source_ref, metadata in _bundle_identity_metadata_evidence(bundle):
        scoped = allowed_metadata.get(source_ref)
        if scoped is None:
            errors.append(
                "Source metadata evidence outside extraction packet: "
                f"source_ref={source_ref}"
            )
            continue
        unsupported = sorted(set(metadata) - set(scoped))
        if unsupported:
            errors.append(
                "Source metadata evidence uses fields outside packet scope: "
                + ", ".join(unsupported)
            )
            continue
        for key, value in metadata.items():
            if scoped.get(key) != value:
                errors.append(
                    "Source metadata evidence does not match packet value: "
                    f"source_ref={source_ref} field={key}"
                )
    return errors



_DURABLE_TEXT_FIELDS = {
    "canonical_name",
    "description",
    "object_text",
    "claim_text",
    "title",
    "excerpt",
}


def _canonical_bundle_json(bundle: dict[str, Any]) -> str:
    return json.dumps(
        bundle,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bundle_sha256(bundle: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_bundle_json(bundle).encode("utf-8")
    ).hexdigest()


def _encoding_loss_errors(bundle: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if (
                    key in _DURABLE_TEXT_FIELDS
                    and isinstance(child, str)
                    and "???" in child
                ):
                    errors.append(
                        f"Possible text encoding loss at {child_path}: "
                        "contains a run of replacement question marks. "
                        "Use UTF-8 JSON file input instead of piping "
                        "Unicode source code through Windows PowerShell 5.1."
                    )
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(bundle)
    return errors


def requeue_extraction_job(
    job_id: str | uuid.UUID,
    *,
    reason: str = "administratively requeued",
    reset_attempts: bool = False,
) -> dict[str, Any]:
    parsed_job_id = _uuid(job_id, "job_id")
    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(
                f"Extraction job not found: {job_id}"
            )
        if job.status == "completed":
            raise KnowledgeExtractionError(
                "Completed extraction jobs cannot be requeued in place"
            )

        job.status = "pending"
        job.lease_token = None
        job.leased_at = None
        job.lease_expires_at = None
        job.available_at = _now()
        job.bundle_json = {}
        _record_quality_event(
            session,
            job,
            event_type="administrative_requeue",
            details=reason,
        )
        job.apply_json = {}
        job.completed_at = None
        job.last_error = reason[:4000]
        if reset_attempts:
            state = dict(job.validation_json or {})
            state["quality_attempt_offset"] = _quality_attempt(job)
            job.validation_json = state
            job.attempts = 0
        session.commit()

        return {
            "job_id": str(job.id),
            "status": job.status,
            "attempts": job.attempts,
            "reason": reason,
        }


def reextract_extraction_job_as_current_version(
    job_id: str | uuid.UUID,
    *,
    reason: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or explicitly schedule a terminal source as the current version."""
    parsed_job_id = _uuid(job_id, "job_id")
    settings = get_settings()
    with SessionLocal() as session:
        source_job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if source_job is None:
            raise KnowledgeExtractionError(
                f"Extraction job not found: {job_id}"
            )
        if source_job.status in ACTIVE_JOB_STATUSES:
            return {
                "ok": False,
                "applied": False,
                "reason": "source_job_is_active",
                "job_id": str(source_job.id),
                "status": source_job.status,
                "extractor_version": source_job.extractor_version,
                "runtime_packet_contract_version": PACKET_CONTRACT_VERSION,
            }
        if source_job.status not in TERMINAL_JOB_STATUSES:
            raise KnowledgeExtractionError(
                f"Unsupported source job status: {source_job.status}"
            )
        current_hash = _source_hash(
            session,
            source_job.source_type,
            source_job.source_id,
        )
        if current_hash != source_job.source_content_hash:
            raise KnowledgeExtractionError(
                "Source content hash changed; enqueue the newly indexed source "
                "instead of re-extracting the historical job."
            )

        same_hash_jobs = list(
            session.scalars(
                select(KnowledgeExtractionJob)
                .where(
                    KnowledgeExtractionJob.source_type == source_job.source_type,
                    KnowledgeExtractionJob.source_id == source_job.source_id,
                    KnowledgeExtractionJob.source_content_hash
                    == source_job.source_content_hash,
                )
                .order_by(KnowledgeExtractionJob.created_at.desc())
            )
        )
        active = next(
            (item for item in same_hash_jobs if item.status in ACTIVE_JOB_STATUSES),
            None,
        )
        if active is not None:
            return {
                "ok": False,
                "applied": False,
                "reason": "active_job_already_exists",
                "job_id": str(active.id),
                "status": active.status,
                "extractor_version": active.extractor_version,
            }
        existing_current = next(
            (
                item
                for item in same_hash_jobs
                if item.extractor_version
                == settings.knowledge_extraction_version
            ),
            None,
        )
        action = "requeue_current_version" if existing_current else "create_v2_job"
        preview = {
            "ok": True,
            "applied": bool(apply),
            "action": action,
            "source_job_id": str(source_job.id),
            "source_extractor_version": source_job.extractor_version,
            "target_extractor_version": settings.knowledge_extraction_version,
            "runtime_packet_contract_version": PACKET_CONTRACT_VERSION,
            "source_ref": _source_ref(source_job.source_type, source_job.source_id),
            "source_content_hash": source_job.source_content_hash,
            "reason": reason,
        }
        if not apply:
            return preview

        if existing_current is not None:
            target_job = existing_current
            target_job.status = "pending"
            target_job.available_at = _now()
            target_job.lease_token = None
            target_job.leased_at = None
            target_job.lease_expires_at = None
            target_job.bundle_json = {}
            target_job.apply_json = {}
            target_job.completed_at = None
            target_job.last_error = reason[:4000]
            _record_quality_event(
                session,
                target_job,
                event_type="controlled_reextract",
                details=reason,
                settings=settings,
            )
            state = dict(target_job.validation_json or {})
            state["quality_attempt_offset"] = _quality_attempt(target_job)
            target_job.validation_json = state
            target_job.attempts = 0
        else:
            target_job = KnowledgeExtractionJob(
                source_type=source_job.source_type,
                source_id=source_job.source_id,
                source_content_hash=source_job.source_content_hash,
                extractor_version=settings.knowledge_extraction_version,
                status="pending",
                priority=source_job.priority,
                attempts=0,
                bundle_json={},
                validation_json={},
                apply_json={},
                last_error=reason[:4000],
            )
            session.add(target_job)
        session.flush()
        session.commit()
        return {
            **preview,
            "job_id": str(target_job.id),
            "status": target_job.status,
        }

def validate_extraction_job(
    job_id: str | uuid.UUID,
    lease_token: str | uuid.UUID,
    bundle_json: str,
) -> dict[str, Any]:
    parsed_job_id = _uuid(job_id, "job_id")
    parsed_token = _uuid(lease_token, "lease_token")
    settings = get_settings()
    try:
        bundle = json.loads(bundle_json)
    except json.JSONDecodeError as exc:
        with SessionLocal() as session:
            job = session.get(KnowledgeExtractionJob, parsed_job_id)
            if job is not None:
                _record_quality_event(
                    session,
                    job,
                    event_type="invalid_json",
                    details=f"{exc.msg} at line {exc.lineno}",
                    settings=settings,
                )
                session.commit()
        raise KnowledgeExtractionError(
            f"Invalid extraction JSON: {exc.msg} at line {exc.lineno}"
        ) from exc
    if not isinstance(bundle, dict):
        with SessionLocal() as session:
            job = session.get(KnowledgeExtractionJob, parsed_job_id)
            if job is not None:
                _record_quality_event(
                    session,
                    job,
                    event_type="invalid_json",
                    details="Extraction bundle is not a JSON object",
                    settings=settings,
                )
                session.commit()
        raise KnowledgeExtractionError("Extraction bundle must be a JSON object")

    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(f"Extraction job not found: {job_id}")
        lease_error = None
        if job.status != "leased":
            lease_error = f"Extraction job is not leased: status={job.status}"
        elif job.lease_token != parsed_token:
            lease_error = "Extraction lease token does not match"
        elif job.lease_expires_at and job.lease_expires_at < _now():
            lease_error = "Extraction lease expired"
        if lease_error:
            _record_quality_event(
                session,
                job,
                event_type="lease_mismatch",
                details=lease_error,
                settings=settings,
            )
            session.commit()
            raise KnowledgeExtractionError(lease_error)

    packet = build_extraction_packet(parsed_job_id)
    scope_errors = _check_evidence_scope(packet, bundle)
    encoding_errors = _encoding_loss_errors(bundle)
    candidate_resolution = packet.get("identity_resolution") or {}
    identity_context_errors = list(
        candidate_resolution.get("blocking_errors") or []
    )
    duplicate_risk = duplicate_risk_diagnostics(
        bundle,
        packet.get("identity_candidates") or [],
        threshold=settings.knowledge_identity_duplicate_risk_threshold,
    )
    ontology = ontology_diagnostics(
        bundle,
        packet.get("identity_candidates") or [],
        threshold=settings.knowledge_identity_duplicate_risk_threshold,
    )
    try:
        validation = import_knowledge_bundle(
            json.dumps(bundle, ensure_ascii=False),
            apply=False,
            created_by="extractor",
        )
    except Exception as exc:
        validation = {
            "ok": False,
            "apply": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    bundle_sha256 = _bundle_sha256(bundle)
    validation = {
        **validation,
        "bundle_sha256": bundle_sha256,
        "candidate_resolution": candidate_resolution,
        "duplicate_risk": duplicate_risk,
        "ontology_diagnostics": ontology,
    }
    policy_errors = [
        *scope_errors,
        *encoding_errors,
        *identity_context_errors,
    ]
    if policy_errors:
        validation = {
            **validation,
            "ok": False,
            "scope_errors": scope_errors,
            "encoding_errors": encoding_errors,
            "identity_context_errors": identity_context_errors,
            "errors": [
                *(validation.get("errors") or []),
                *policy_errors,
            ],
        }
    validation["quality"] = {
        "existing_entities_reused": int(validation.get("existing_entities") or 0),
        "new_entities_proposed": int(validation.get("new_entities") or 0),
        "duplicate_risk_count": int(duplicate_risk.get("count") or 0),
        "ontology_warning_count": int(ontology.get("warning_count") or 0),
    }

    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(f"Extraction job not found: {job_id}")
        if job.status != "leased":
            lease_error = f"Extraction job is not leased: status={job.status}"
            _record_quality_event(
                session,
                job,
                event_type="lease_mismatch",
                details=lease_error,
                settings=settings,
            )
            session.commit()
            raise KnowledgeExtractionError(lease_error)
        if job.lease_token != parsed_token:
            _record_quality_event(
                session,
                job,
                event_type="lease_mismatch",
                details="Extraction lease token does not match",
                settings=settings,
            )
            session.commit()
            raise KnowledgeExtractionError("Extraction lease token does not match")
        if job.lease_expires_at and job.lease_expires_at < _now():
            _record_quality_event(
                session,
                job,
                event_type="lease_mismatch",
                details="Extraction lease expired",
                settings=settings,
            )
            session.commit()
            raise KnowledgeExtractionError("Extraction lease expired")

        history = _quality_history(job.validation_json)
        if not validation.get("ok"):
            event_type = "semantic_validation_failure"
            if identity_context_errors:
                event_type = "incomplete_identity_context"
            elif scope_errors or encoding_errors:
                event_type = "encoding_or_scope_validation_failure"
            history = _append_quality_event(
                history,
                attempt=_quality_attempt(job),
                event_type=event_type,
                details="; ".join(validation.get("errors") or []),
                limit=settings.knowledge_extraction_quality_history_limit,
            )
        validation = _validation_state(
            job.validation_json,
            validation,
            history=history,
        )

        job.bundle_json = bundle
        job.validation_json = validation
        job.lease_token = None
        job.leased_at = None
        job.lease_expires_at = None
        if validation.get("ok"):
            job.status = "validated"
            job.last_error = None
        else:
            exhausted = job.attempts >= settings.knowledge_extraction_max_attempts
            job.status = "error" if exhausted else "pending"
            job.last_error = "; ".join(validation.get("errors") or [])[:4000]
            if exhausted:
                job.completed_at = _now()
            else:
                job.available_at = _now() + timedelta(minutes=1)
        final_status = job.status
        session.commit()

    return {
        "job_id": str(parsed_job_id),
        "status": final_status,
        "validation": validation,
    }


def apply_validated_extraction_job(
    job_id: str | uuid.UUID,
) -> dict[str, Any]:
    parsed_job_id = _uuid(job_id, "job_id")
    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(f"Extraction job not found: {job_id}")
        if job.status != "validated":
            raise KnowledgeExtractionError(
                f"Extraction job must be validated before apply: status={job.status}"
            )
        current_hash = _source_hash(session, job.source_type, job.source_id)
        if current_hash != job.source_content_hash:
            _record_quality_event(
                session,
                job,
                event_type="source_fingerprint_mismatch",
                details="Source content changed after validation",
            )
            job.status = "superseded"
            job.last_error = "Source content changed after validation; requeue required."
            job.completed_at = _now()
            session.commit()
            raise KnowledgeExtractionError(
                "Source content changed after validation; extraction job was superseded."
            )
        bundle = job.bundle_json or {}
        bundle_json = json.dumps(bundle, ensure_ascii=False)
        expected_bundle_sha256 = (
            job.validation_json or {}
        ).get("bundle_sha256")
        actual_bundle_sha256 = _bundle_sha256(bundle)
        if (
            not expected_bundle_sha256
            or expected_bundle_sha256 != actual_bundle_sha256
        ):
            _record_quality_event(
                session,
                job,
                event_type="fingerprint_mismatch",
                details=(
                    "Validated bundle fingerprint is missing or differs from "
                    "the current bundle"
                ),
            )
            job.status = "pending"
            job.bundle_json = {}
            job.apply_json = {}
            job.last_error = (
                "Validated bundle fingerprint is missing or changed; "
                "job was requeued before apply."
            )
            job.available_at = _now() + timedelta(minutes=1)
            session.commit()
            raise KnowledgeExtractionError(
                "Validated bundle fingerprint mismatch; extraction "
                "job was requeued."
            )

    # Revalidate immediately before persistence. Chunk rows can be rebuilt by
    # a later re-index even when the source content hash is unchanged. A stale
    # evidence chunk ID must therefore never be applied from an old bundle.
    try:
        preflight = import_knowledge_bundle(
            bundle_json,
            apply=False,
            created_by="extractor",
        )
    except Exception as exc:
        preflight = {
            "ok": False,
            "apply": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    if not preflight.get("ok"):
        with SessionLocal() as session:
            job = session.get(KnowledgeExtractionJob, parsed_job_id)
            if job is not None:
                history = _append_quality_event(
                    _quality_history(job.validation_json),
                    attempt=_quality_attempt(job),
                    event_type="preflight_failure",
                    details="; ".join(preflight.get("errors") or []),
                    limit=get_settings().knowledge_extraction_quality_history_limit,
                )
                exhausted = (
                    job.attempts
                    >= get_settings().knowledge_extraction_max_attempts
                )
                job.status = "error" if exhausted else "pending"
                job.bundle_json = {}
                job.validation_json = _validation_state(
                    job.validation_json,
                    preflight,
                    history=history,
                )
                job.apply_json = {}
                job.last_error = (
                    "Validated extraction became stale before apply: "
                    + "; ".join(preflight.get("errors") or [])
                )[:4000]
                if exhausted:
                    job.completed_at = _now()
                else:
                    job.available_at = _now() + timedelta(minutes=1)
                session.commit()
        raise KnowledgeExtractionError(
            "Validated extraction became stale before apply and was requeued."
        )

    try:
        result = import_knowledge_bundle(
            bundle_json,
            apply=True,
            created_by="extractor",
        )
    except Exception as exc:
        result = {
            "ok": False,
            "apply": True,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(
                f"Extraction job disappeared: {job_id}"
            )
        job.apply_json = result
        if result.get("ok"):
            job.status = "completed"
            job.completed_at = _now()
            job.last_error = None
            final_status = "completed"
        else:
            history = _append_quality_event(
                _quality_history(job.validation_json),
                attempt=_quality_attempt(job),
                event_type="apply_failure",
                details="; ".join(result.get("errors") or []),
                limit=get_settings().knowledge_extraction_quality_history_limit,
            )
            job.validation_json = _validation_state(
                job.validation_json,
                history=history,
            )
            exhausted = (
                job.attempts
                >= get_settings().knowledge_extraction_max_attempts
            )
            job.status = "error" if exhausted else "pending"
            job.last_error = (
                "Apply semantic validation failed: "
                + "; ".join(result.get("errors") or [])
            )[:4000]
            if exhausted:
                job.completed_at = _now()
            else:
                job.bundle_json = {}
                job.available_at = _now() + timedelta(minutes=1)
            final_status = job.status
        session.commit()
    return {
        "job_id": str(parsed_job_id),
        "status": final_status,
        "apply_result": result,
    }


def release_extraction_job(
    job_id: str | uuid.UUID,
    lease_token: str | uuid.UUID,
    *,
    reason: str = "released by agent",
    retry: bool = True,
) -> dict[str, Any]:
    parsed_job_id = _uuid(job_id, "job_id")
    parsed_token = _uuid(lease_token, "lease_token")
    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, parsed_job_id)
        if job is None:
            raise KnowledgeExtractionError(f"Extraction job not found: {job_id}")
        if job.status != "leased" or job.lease_token != parsed_token:
            _record_quality_event(
                session,
                job,
                event_type="lease_mismatch",
                details="Extraction job release lease does not match",
            )
            session.commit()
            raise KnowledgeExtractionError("Extraction job lease does not match")
        exhausted = job.attempts >= get_settings().knowledge_extraction_max_attempts
        job.status = (
            "error"
            if retry and exhausted
            else ("pending" if retry else "skipped")
        )
        job.lease_token = None
        job.leased_at = None
        job.lease_expires_at = None
        job.last_error = reason[:4000]
        if retry and not exhausted:
            job.available_at = _now() + timedelta(minutes=1)
        else:
            job.completed_at = _now()
        session.commit()
        return {
            "job_id": str(job.id),
            "status": job.status,
            "reason": reason,
        }


def _quality_metrics(
    rows: list[tuple[str, dict[str, Any]]],
    counts: dict[str, int],
) -> dict[str, int]:
    metrics = {
        "exact_candidate_hits": 0,
        "semantic_candidate_hits": 0,
        "lexical_candidate_hits": 0,
        "merged_identity_candidate_count": 0,
        "existing_entities_reused": 0,
        "new_entities_proposed": 0,
        "duplicate_risk_count": 0,
        "ontology_warning_count": 0,
        "validation_failures": 0,
        "skipped_jobs": int(counts.get("skipped", 0)),
        "completed_jobs": int(counts.get("completed", 0)),
        "validated_jobs": int(counts.get("validated", 0)),
    }
    for _status, raw in rows:
        data = raw if isinstance(raw, dict) else {}
        candidates = data.get("candidate_resolution") or {}
        quality = data.get("quality") or {}
        for key in (
            "exact_candidate_hits",
            "semantic_candidate_hits",
            "lexical_candidate_hits",
            "merged_identity_candidate_count",
        ):
            metrics[key] += int(candidates.get(key) or 0)
        for key in (
            "existing_entities_reused",
            "new_entities_proposed",
            "duplicate_risk_count",
            "ontology_warning_count",
        ):
            metrics[key] += int(quality.get(key) or 0)
        metrics["validation_failures"] += _quality_failure_total(data)
    return metrics


def extraction_status() -> dict[str, Any]:
    settings = get_settings()
    now = _now()
    with SessionLocal() as session:
        rows = session.execute(
            select(
                KnowledgeExtractionJob.status,
                func.count(KnowledgeExtractionJob.id),
            ).group_by(KnowledgeExtractionJob.status)
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        quality_rows = [
            (str(status), validation_json or {})
            for status, validation_json in session.execute(
                select(
                    KnowledgeExtractionJob.status,
                    KnowledgeExtractionJob.validation_json,
                )
            ).all()
        ]
        expired = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeExtractionJob)
                .where(
                    KnowledgeExtractionJob.status == "leased",
                    KnowledgeExtractionJob.lease_expires_at <= now,
                )
            )
            or 0
        )
        oldest_pending = session.scalar(
            select(func.min(KnowledgeExtractionJob.created_at)).where(
                KnowledgeExtractionJob.status == "pending"
            )
        )
        recent_errors = list(
            session.execute(
                select(
                    KnowledgeExtractionJob.id,
                    KnowledgeExtractionJob.source_type,
                    KnowledgeExtractionJob.source_id,
                    KnowledgeExtractionJob.last_error,
                    KnowledgeExtractionJob.updated_at,
                )
                .where(KnowledgeExtractionJob.status == "error")
                .order_by(KnowledgeExtractionJob.updated_at.desc())
                .limit(10)
            )
        )
        return {
            "enabled": settings.knowledge_extraction_enabled,
            "auto_queue": settings.knowledge_extraction_auto_queue,
            "extractor_version": settings.knowledge_extraction_version,
            "provider": settings.knowledge_extraction_provider,
            "auto_apply": settings.knowledge_extraction_auto_apply,
            "counts": counts,
            "total": sum(counts.values()),
            "expired_leases": expired,
            "oldest_pending_at": (
                oldest_pending.isoformat() if oldest_pending else None
            ),
            "recent_errors": [
                {
                    "job_id": str(row.id),
                    "source_ref": _source_ref(row.source_type, row.source_id),
                    "error": row.last_error,
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in recent_errors
            ],
            "quality": _quality_metrics(quality_rows, counts),
        }


def backfill_extraction_queue(
    *,
    source_types: list[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 1000,
    apply: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    selected = source_types or ["email", "attachment", "document"]
    selected = list(dict.fromkeys(value.strip().lower() for value in selected))
    invalid = [value for value in selected if value not in SUPPORTED_SOURCE_TYPES]
    if invalid:
        raise KnowledgeExtractionError(
            f"Unsupported source types: {', '.join(invalid)}"
        )
    safe_limit = max(1, min(int(limit), 100_000))
    # Give every requested source class a chance to participate. Fetch a
    # slightly oversized per-type window, merge by source date, then apply the
    # global limit. This avoids the default all-types backfill being consumed
    # entirely by email rows.
    per_type_limit = max(1, (safe_limit + len(selected) - 1) // len(selected))
    candidates: list[tuple[str, uuid.UUID, datetime | None]] = []

    with SessionLocal() as session:
        if "email" in selected:
            statement = select(
                EmailMessage.id,
                func.coalesce(EmailMessage.received_at, EmailMessage.sent_at),
            ).where(EmailMessage.processing_status == "indexed")
            if date_from:
                statement = statement.where(
                    func.coalesce(EmailMessage.received_at, EmailMessage.sent_at)
                    >= date_from
                )
            if date_to:
                statement = statement.where(
                    func.coalesce(EmailMessage.received_at, EmailMessage.sent_at)
                    <= date_to
                )
            for source_id, source_date in session.execute(
                statement.order_by(
                    func.coalesce(
                        EmailMessage.received_at,
                        EmailMessage.sent_at,
                        EmailMessage.created_at,
                    ).desc()
                ).limit(per_type_limit)
            ):
                candidates.append(("email", source_id, source_date))

        if "attachment" in selected:
            statement = (
                select(
                    Attachment.id,
                    func.coalesce(EmailMessage.received_at, EmailMessage.sent_at),
                )
                .join(EmailMessage, EmailMessage.id == Attachment.message_id)
                .where(Attachment.processing_status == "indexed")
            )
            if date_from:
                statement = statement.where(
                    func.coalesce(EmailMessage.received_at, EmailMessage.sent_at)
                    >= date_from
                )
            if date_to:
                statement = statement.where(
                    func.coalesce(EmailMessage.received_at, EmailMessage.sent_at)
                    <= date_to
                )
            for source_id, source_date in session.execute(
                statement.order_by(
                    func.coalesce(
                        EmailMessage.received_at,
                        EmailMessage.sent_at,
                    ).desc()
                ).limit(per_type_limit)
            ):
                candidates.append(("attachment", source_id, source_date))

        if "document" in selected:
            statement = select(
                ExternalDocument.id,
                ExternalDocument.source_date,
            ).where(ExternalDocument.processing_status == "indexed")
            if date_from:
                statement = statement.where(ExternalDocument.source_date >= date_from)
            if date_to:
                statement = statement.where(ExternalDocument.source_date <= date_to)
            for source_id, source_date in session.execute(
                statement.order_by(ExternalDocument.source_date.desc()).limit(
                    per_type_limit
                )
            ):
                candidates.append(("document", source_id, source_date))

        floor = datetime.min.replace(tzinfo=UTC)
        candidates.sort(
            key=lambda item: item[2] or floor,
            reverse=True,
        )
        candidates = candidates[:safe_limit]
        preview = [
            {
                "source_type": source_type,
                "source_ref": _source_ref(source_type, source_id),
                "source_date": source_date.isoformat() if source_date else None,
            }
            for source_type, source_id, source_date in candidates
        ]
        if not apply:
            return {
                "apply": False,
                "candidate_count": len(preview),
                "candidates": preview[:100],
                "note": "Dry run. Repeat with apply=true to enqueue.",
            }

        counters = {
            "queued": 0,
            "already_queued": 0,
            "skipped": 0,
            "queue_error": 0,
        }
        results: list[dict[str, Any]] = []
        for source_type, source_id, _ in candidates:
            result = try_enqueue_indexed_source(
                session,
                source_type=source_type,
                source_id=source_id,
                settings=settings,
                reason="backfill",
            )
            results.append(result)
            if result.get("queued"):
                counters["queued"] += 1
            elif result.get("reason") == "already_queued":
                counters["already_queued"] += 1
            elif result.get("reason") == "queue_error":
                counters["queue_error"] += 1
            else:
                counters["skipped"] += 1
        session.commit()
        return {
            "apply": True,
            "candidate_count": len(candidates),
            "counters": counters,
            "results": results[:100],
        }
