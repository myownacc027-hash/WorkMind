from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from itertools import islice
from typing import Iterable, Iterator, TypeVar

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.common.config import Settings
from packages.database.models import (
    DocumentChunk,
    EmailMessage,
    ProcessingJob,
)
from packages.processing.chunking import (
    build_email_document,
    chunk_text,
)
from packages.processing.embeddings import embed_texts
from packages.search.indexer import (
    delete_points,
    upsert_chunks,
)
from packages.search.qdrant import get_qdrant_client

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
                ProcessingJob.job_type == "index_email",
                ProcessingJob.source_type == "email",
                ProcessingJob.available_at
                <= datetime.now(UTC),
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


def _payload_for(
    message: EmailMessage,
    chunk: DocumentChunk,
) -> dict:
    occurred_at = (
        message.received_at or message.sent_at
    )
    return {
        "chunk_id": str(chunk.id),
        "source_type": "email",
        "source_id": str(message.id),
        "thread_id": (
            str(message.thread_id)
            if message.thread_id
            else None
        ),
        "mailbox_id": str(message.mailbox_id),
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


def _mark_failed(
    session: Session,
    *,
    job_ids: list[uuid.UUID],
    settings: Settings,
    error: Exception,
) -> None:
    for job_id in job_ids:
        job = session.get(
            ProcessingJob,
            job_id,
        )
        if job is None:
            continue
        job.status = (
            "error"
            if job.attempts
            >= settings.worker_max_attempts
            else "pending"
        )
        job.last_error = str(error)
        job.locked_at = None
    session.commit()


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

    message_rows: dict[uuid.UUID, list[DocumentChunk]] = {}
    rows: list[DocumentChunk] = []
    row_messages: list[EmailMessage] = []
    old_point_ids: list[uuid.UUID] = []

    for job in jobs:
        if (
            job.job_type != "index_email"
            or job.source_type != "email"
        ):
            raise ValueError(
                "Unsupported job: "
                f"{job.job_type}/{job.source_type}"
            )

        message = session.get(
            EmailMessage,
            job.source_id,
        )
        if message is None:
            raise LookupError(
                f"Email message not found: {job.source_id}"
            )

        occurred_at = (
            message.received_at or message.sent_at
        )
        document = build_email_document(
            subject=message.subject,
            sender_name=message.sender_name,
            sender_email=message.sender_email,
            occurred_at=(
                occurred_at.isoformat()
                if occurred_at
                else None
            ),
            body_text=message.body_text,
        )
        chunks = chunk_text(
            document,
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )

        existing = list(
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.source_type == "email",
                    DocumentChunk.source_id == message.id,
                )
            )
        )
        old_point_ids.extend(
            item.qdrant_point_id
            for item in existing
            if item.qdrant_point_id
        )
        session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.source_type == "email",
                DocumentChunk.source_id == message.id,
            )
        )

        current_rows: list[DocumentChunk] = []
        for chunk in chunks:
            chunk_hash = hashlib.sha256(
                chunk.text.encode(
                    "utf-8",
                    errors="replace",
                )
            ).hexdigest()
            row = DocumentChunk(
                source_type="email",
                source_id=message.id,
                chunk_index=chunk.index,
                text=chunk.text,
                content_hash=chunk_hash,
                qdrant_point_id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "workmind:email:"
                    f"{message.id}:{chunk.index}",
                ),
                embedding_model=settings.embedding_model,
                embedding_status="processing",
                metadata_json={
                    "subject": message.subject,
                    "thread_id": (
                        str(message.thread_id)
                        if message.thread_id
                        else None
                    ),
                },
            )
            session.add(row)
            rows.append(row)
            current_rows.append(row)
            row_messages.append(message)
        message_rows[message.id] = current_rows

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
        _payload_for(message, row)
        for message, row in zip(
            row_messages,
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

    for row in rows:
        row.embedding_status = "indexed"

    now = datetime.now(UTC)
    for job in jobs:
        message = session.get(
            EmailMessage,
            job.source_id,
        )
        if message is not None:
            message.processing_status = (
                "indexed"
                if message_rows.get(message.id)
                else "empty"
            )
            if (
                settings.knowledge_extraction_auto_queue
                and message.processing_status == "indexed"
            ):
                from packages.knowledge.extraction import (
                    try_enqueue_indexed_source,
                )

                try_enqueue_indexed_source(
                    session,
                    source_type="email",
                    source_id=message.id,
                    settings=settings,
                    reason="email_indexed",
                )
        job.status = "completed"
        job.completed_at = now
        job.last_error = None
        job.locked_at = None

    session.commit()
    logger.info(
        "processing_batch_completed",
        jobs=len(jobs),
        chunks=len(rows),
    )
    return {
        "completed": len(jobs),
        "chunks": len(rows),
    }


def run_batch(
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
            "failed": 0,
            **result,
        }
    except Exception as exc:
        with session_factory() as session:
            _mark_failed(
                session,
                job_ids=job_ids,
                settings=settings,
                error=exc,
            )
        logger.exception(
            "processing_batch_failed",
            jobs=len(job_ids),
            error=str(exc),
        )
        return {
            "claimed": len(job_ids),
            "completed": 0,
            "failed": len(job_ids),
            "chunks": 0,
        }
