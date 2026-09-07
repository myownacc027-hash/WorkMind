from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Iterable, Iterator, TypeVar

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.common.config import Settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailMessage,
    ProcessingJob,
)
from packages.processing.attachments import (
    AttachmentExtractionError,
    extract_attachment,
)
from packages.processing.chunking import (
    build_attachment_document,
    chunk_text,
)
from packages.processing.embeddings import embed_texts
from packages.search.indexer import delete_points, upsert_chunks
from packages.search.qdrant import get_qdrant_client
from packages.storage import resolve_attachment_path

logger = structlog.get_logger(__name__)
T = TypeVar("T")


def _batched(
    values: Iterable[T],
    size: int,
) -> Iterator[list[T]]:
    iterator = iter(values)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def _claim_jobs(
    session: Session,
    *,
    batch_size: int,
) -> list[uuid.UUID]:
    jobs = list(
        session.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.status == "pending",
                ProcessingJob.job_type == "index_attachment",
                ProcessingJob.source_type == "attachment",
                ProcessingJob.available_at <= datetime.now(UTC),
            )
            .order_by(ProcessingJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        )
    )
    now = datetime.now(UTC)
    for job in jobs:
        job.status = "processing"
        job.locked_at = now
        job.attempts += 1
    session.commit()
    return [job.id for job in jobs]


def _payload(
    attachment: Attachment,
    message: EmailMessage,
    chunk: DocumentChunk,
) -> dict:
    occurred_at = message.received_at or message.sent_at
    return {
        "chunk_id": str(chunk.id),
        "source_type": "attachment",
        "source_id": str(attachment.id),
        "source_ref": f"workmind://attachment/{attachment.id}",
        "parent_source_id": str(message.id),
        "parent_source_ref": f"workmind://email/{message.id}",
        "thread_id": (
            str(message.thread_id)
            if message.thread_id
            else None
        ),
        "mailbox_id": str(message.mailbox_id),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "subject": message.subject,
        "sender_name": message.sender_name,
        "sender_email": message.sender_email,
        "folder_path": message.folder_path,
        "source_date": (
            occurred_at.isoformat()
            if occurred_at
            else None
        ),
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "content_hash": chunk.content_hash,
    }


def _terminal_job(
    job: ProcessingJob,
    attachment: Attachment,
    *,
    status: str,
    error: str | None,
    metadata: dict,
    method: str | None,
) -> None:
    now = datetime.now(UTC)
    attachment.processing_status = status
    attachment.last_error = error
    attachment.metadata_json = metadata
    attachment.extraction_method = method
    attachment.processed_at = now
    attachment.extracted_chars = 0
    attachment.extracted_text_path = None
    attachment.page_count = metadata.get("page_count")
    attachment.sheet_count = metadata.get("sheet_count")
    job.status = "completed"
    job.completed_at = now
    job.last_error = error
    job.locked_at = None


def _process_claimed_batch(
    session: Session,
    *,
    job_ids: list[uuid.UUID],
    settings: Settings,
) -> dict[str, int]:
    jobs = [
        session.get(ProcessingJob, job_id)
        for job_id in job_ids
    ]
    jobs = [job for job in jobs if job is not None]

    rows: list[DocumentChunk] = []
    row_contexts: list[tuple[Attachment, EmailMessage]] = []
    old_point_ids: list[uuid.UUID] = []
    successful_jobs: list[tuple[ProcessingJob, Attachment]] = []
    attachment_row_counts: dict[uuid.UUID, int] = {}
    failed_jobs = 0
    terminal_without_chunks = 0

    for job in jobs:
        attachment = session.get(Attachment, job.source_id)
        if attachment is None:
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.last_error = "Attachment row no longer exists."
            job.locked_at = None
            terminal_without_chunks += 1
            continue

        message = session.get(EmailMessage, attachment.message_id)
        if message is None:
            _terminal_job(
                job,
                attachment,
                status="missing_parent",
                error="Parent email no longer exists.",
                metadata={},
                method=None,
            )
            terminal_without_chunks += 1
            continue

        existing = list(
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.source_type == "attachment",
                    DocumentChunk.source_id == attachment.id,
                )
            )
        )
        old_point_ids.extend(
            chunk.qdrant_point_id
            for chunk in existing
            if chunk.qdrant_point_id
        )
        session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.source_type == "attachment",
                DocumentChunk.source_id == attachment.id,
            )
        )

        resolved_path = resolve_attachment_path(
            attachment
        )
        path = (
            resolved_path
            if resolved_path is not None
            else Path("")
        )
        attachment.processing_status = "processing"
        attachment.last_error = None

        try:
            result = extract_attachment(
                path,
                max_bytes=settings.attachment_max_bytes,
                max_chars=settings.attachment_max_extracted_chars,
                supported_extensions=(
                    settings.attachment_supported_extensions
                ),
                filename_hint=attachment.filename,
            )
        except AttachmentExtractionError as exc:
            failed_jobs += 1
            attachment.processing_status = (
                "error"
                if job.attempts >= settings.worker_max_attempts
                else "queued"
            )
            attachment.last_error = str(exc)
            attachment.processed_at = datetime.now(UTC)
            job.status = (
                "error"
                if job.attempts >= settings.worker_max_attempts
                else "pending"
            )
            job.last_error = str(exc)
            job.locked_at = None
            continue

        if result.status != "extracted":
            _terminal_job(
                job,
                attachment,
                status=result.status,
                error=result.error,
                metadata=result.metadata,
                method=result.method,
            )
            terminal_without_chunks += 1
            continue

        output_path = (
            settings.attachment_text_dir
            / f"{attachment.id}.txt"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            result.text,
            encoding="utf-8",
        )

        occurred_at = message.received_at or message.sent_at
        document = build_attachment_document(
            filename=attachment.filename,
            subject=message.subject,
            sender_name=message.sender_name,
            sender_email=message.sender_email,
            occurred_at=(
                occurred_at.isoformat()
                if occurred_at
                else None
            ),
            extracted_text=result.text,
        )
        chunks = chunk_text(
            document,
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )

        attachment.extracted_text_path = str(output_path)
        attachment.extraction_method = result.method
        attachment.extracted_chars = len(result.text)
        attachment.page_count = result.metadata.get("page_count")
        attachment.sheet_count = result.metadata.get("sheet_count")
        attachment.metadata_json = result.metadata
        attachment.last_error = None

        attachment_row_counts[attachment.id] = len(chunks)
        for chunk in chunks:
            chunk_hash = hashlib.sha256(
                chunk.text.encode(
                    "utf-8",
                    errors="replace",
                )
            ).hexdigest()
            row = DocumentChunk(
                source_type="attachment",
                source_id=attachment.id,
                chunk_index=chunk.index,
                text=chunk.text,
                content_hash=chunk_hash,
                qdrant_point_id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "workmind:attachment:"
                    f"{attachment.id}:{chunk.index}",
                ),
                embedding_model=settings.embedding_model,
                embedding_status="processing",
                metadata_json={
                    "filename": attachment.filename,
                    "message_id": str(message.id),
                    "subject": message.subject,
                },
            )
            session.add(row)
            rows.append(row)
            row_contexts.append((attachment, message))

        successful_jobs.append((job, attachment))

    session.flush()
    qdrant = get_qdrant_client()
    delete_points(qdrant, old_point_ids)

    vectors: list[list[float]] = []
    for text_batch in _batched(
        [row.text for row in rows],
        256,
    ):
        vectors.extend(embed_texts(text_batch))

    point_ids = [
        row.qdrant_point_id
        for row in rows
        if row.qdrant_point_id
    ]
    payloads = [
        _payload(attachment, message, row)
        for (attachment, message), row in zip(
            row_contexts,
            rows,
            strict=True,
        )
    ]
    for start in range(0, len(rows), 512):
        end = start + 512
        upsert_chunks(
            qdrant,
            point_ids=point_ids[start:end],
            vectors=vectors[start:end],
            payloads=payloads[start:end],
        )

    now = datetime.now(UTC)
    for row in rows:
        row.embedding_status = "indexed"
    for job, attachment in successful_jobs:
        attachment.processing_status = (
            "indexed"
            if attachment_row_counts.get(attachment.id, 0) > 0
            else "empty_no_text"
        )
        attachment.processed_at = now
        if (
            settings.knowledge_extraction_auto_queue
            and attachment.processing_status == "indexed"
        ):
            from packages.knowledge.extraction import try_enqueue_indexed_source

            try_enqueue_indexed_source(
                session,
                source_type="attachment",
                source_id=attachment.id,
                settings=settings,
                reason="attachment_indexed",
            )
        job.status = "completed"
        job.completed_at = now
        job.last_error = None
        job.locked_at = None

    session.commit()
    logger.info(
        "attachment_batch_completed",
        jobs=len(jobs),
        indexed=len(successful_jobs),
        terminal=terminal_without_chunks,
        failed=failed_jobs,
        chunks=len(rows),
    )
    return {
        "completed": len(jobs) - failed_jobs,
        "failed": failed_jobs,
        "indexed": len(successful_jobs),
        "terminal": terminal_without_chunks,
        "chunks": len(rows),
    }


def run_attachment_batch(
    session_factory,
    *,
    settings: Settings,
    batch_size: int,
) -> dict[str, int]:
    with session_factory() as session:
        job_ids = _claim_jobs(
            session,
            batch_size=batch_size,
        )

    if not job_ids:
        return {
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "indexed": 0,
            "terminal": 0,
            "chunks": 0,
        }

    try:
        with session_factory() as session:
            result = _process_claimed_batch(
                session,
                job_ids=job_ids,
                settings=settings,
            )
        return {
            "claimed": len(job_ids),
            **result,
        }
    except Exception as exc:
        with session_factory() as session:
            now = datetime.now(UTC)
            for job_id in job_ids:
                job = session.get(ProcessingJob, job_id)
                if job is None:
                    continue
                job.status = (
                    "error"
                    if job.attempts >= settings.worker_max_attempts
                    else "pending"
                )
                job.last_error = str(exc)
                job.locked_at = None
                attachment = session.get(
                    Attachment,
                    job.source_id,
                )
                if attachment is not None:
                    attachment.processing_status = (
                        "error"
                        if job.status == "error"
                        else "queued"
                    )
                    attachment.last_error = str(exc)
                    attachment.processed_at = now
            session.commit()
        logger.exception(
            "attachment_batch_failed",
            jobs=len(job_ids),
            error=str(exc),
        )
        return {
            "claimed": len(job_ids),
            "completed": 0,
            "failed": len(job_ids),
            "indexed": 0,
            "terminal": 0,
            "chunks": 0,
        }
