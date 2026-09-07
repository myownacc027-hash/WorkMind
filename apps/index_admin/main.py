from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

import typer
from qdrant_client import QdrantClient, models
from sqlalchemy import func, select

from packages.common.config import get_settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailMessage,
    ExternalDocument,
)
from packages.database.session import SessionLocal
from packages.processing.embeddings import embed_texts
from packages.search.indexer import ensure_collection, upsert_chunks
from packages.search.qdrant import get_qdrant_client

app = typer.Typer(no_args_is_help=True)


def _json(value: dict[str, Any]) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _batched(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


@app.command("check")
def check(
    sample_size: int = typer.Option(200, min=1, max=5000),
) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        indexed_rows = int(
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.embedding_status == "indexed",
                    DocumentChunk.qdrant_point_id.is_not(None),
                )
            )
            or 0
        )
        sample = list(
            session.execute(
                select(
                    DocumentChunk.id,
                    DocumentChunk.qdrant_point_id,
                    DocumentChunk.source_type,
                    DocumentChunk.source_id,
                )
                .where(
                    DocumentChunk.embedding_status == "indexed",
                    DocumentChunk.qdrant_point_id.is_not(None),
                )
                .order_by(DocumentChunk.id)
                .limit(sample_size)
            ).all()
        )

    client = get_qdrant_client()
    collection_exists = client.collection_exists(settings.qdrant_collection)
    if not collection_exists:
        _json({
            "ok": False,
            "database_indexed_chunks": indexed_rows,
            "collection_exists": False,
            "missing_sample_points": len(sample),
        })
        raise typer.Exit(2)

    info = client.get_collection(settings.qdrant_collection)
    point_ids = [str(row.qdrant_point_id) for row in sample]
    retrieved = client.retrieve(
        collection_name=settings.qdrant_collection,
        ids=point_ids,
        with_payload=True,
        with_vectors=False,
    )
    by_id = {str(point.id): point for point in retrieved}
    missing: list[str] = []
    payload_mismatch: list[dict[str, str]] = []
    for row in sample:
        key = str(row.qdrant_point_id)
        point = by_id.get(key)
        if point is None:
            missing.append(key)
            continue
        payload = point.payload or {}
        if (
            str(payload.get("chunk_id")) != str(row.id)
            or str(payload.get("source_type")) != str(row.source_type)
            or str(payload.get("source_id")) != str(row.source_id)
        ):
            payload_mismatch.append({
                "point_id": key,
                "chunk_id": str(row.id),
            })

    points_count = getattr(info, "points_count", None)
    count_delta = (
        int(points_count) - indexed_rows
        if points_count is not None
        else None
    )
    ok = not missing and not payload_mismatch and count_delta == 0
    _json({
        "ok": ok,
        "collection": settings.qdrant_collection,
        "database_indexed_chunks": indexed_rows,
        "qdrant_points_count": points_count,
        "count_delta": count_delta,
        "sample_size": len(sample),
        "missing_sample_points": len(missing),
        "payload_mismatches": len(payload_mismatch),
        "missing_point_ids": missing[:20],
        "payload_mismatch_examples": payload_mismatch[:20],
    })
    if not ok:
        raise typer.Exit(1)


def _payloads_for_batch(
    chunks: list[DocumentChunk],
) -> list[dict[str, Any]]:
    email_ids = {
        chunk.source_id
        for chunk in chunks
        if chunk.source_type == "email"
    }
    attachment_ids = {
        chunk.source_id
        for chunk in chunks
        if chunk.source_type == "attachment"
    }
    document_ids = {
        chunk.source_id
        for chunk in chunks
        if chunk.source_type == "document"
    }
    with SessionLocal() as session:
        emails = {
            item.id: item
            for item in session.scalars(
                select(EmailMessage).where(EmailMessage.id.in_(email_ids))
            )
        } if email_ids else {}
        attachments = {
            item.id: item
            for item in session.scalars(
                select(Attachment).where(Attachment.id.in_(attachment_ids))
            )
        } if attachment_ids else {}
        documents = {
            item.id: item
            for item in session.scalars(
                select(ExternalDocument).where(
                    ExternalDocument.id.in_(document_ids)
                )
            )
        } if document_ids else {}
        parent_ids = {
            attachment.message_id
            for attachment in attachments.values()
        }
        parents = {
            item.id: item
            for item in session.scalars(
                select(EmailMessage).where(EmailMessage.id.in_(parent_ids))
            )
        } if parent_ids else {}

    payloads: list[dict[str, Any]] = []
    for chunk in chunks:
        if chunk.source_type == "email":
            message = emails.get(chunk.source_id)
            if message is None:
                raise LookupError(f"Email not found: {chunk.source_id}")
            occurred_at = message.received_at or message.sent_at
            payloads.append({
                "chunk_id": str(chunk.id),
                "source_type": "email",
                "source_id": str(message.id),
                "thread_id": str(message.thread_id) if message.thread_id else None,
                "mailbox_id": str(message.mailbox_id),
                "subject": message.subject,
                "sender_name": message.sender_name,
                "sender_email": message.sender_email,
                "folder_path": message.folder_path,
                "source_date": occurred_at.isoformat() if occurred_at else None,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "content_hash": chunk.content_hash,
            })
            continue

        if chunk.source_type == "document":
            document = documents.get(chunk.source_id)
            if document is None:
                raise LookupError(
                    f"External document not found: {chunk.source_id}"
                )
            payloads.append({
                "chunk_id": str(chunk.id),
                "source_type": "document",
                "source_id": str(document.id),
                "source_ref": (
                    f"workmind://document/{document.id}"
                ),
                "filename": document.original_filename,
                "mime_type": document.mime_type,
                "source_date": (
                    document.source_date.isoformat()
                    if document.source_date
                    else None
                ),
                "archive_path": document.archive_path,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "content_hash": chunk.content_hash,
                "document_content_hash": document.content_hash,
            })
            continue

        attachment = attachments.get(chunk.source_id)
        if attachment is None:
            raise LookupError(f"Attachment not found: {chunk.source_id}")
        message = parents.get(attachment.message_id)
        if message is None:
            raise LookupError(f"Parent email not found: {attachment.message_id}")
        occurred_at = message.received_at or message.sent_at
        payloads.append({
            "chunk_id": str(chunk.id),
            "source_type": "attachment",
            "source_id": str(attachment.id),
            "parent_source_id": str(message.id),
            "thread_id": str(message.thread_id) if message.thread_id else None,
            "mailbox_id": str(message.mailbox_id),
            "subject": message.subject,
            "filename": attachment.filename,
            "mime_type": attachment.mime_type,
            "sender_name": message.sender_name,
            "sender_email": message.sender_email,
            "folder_path": message.folder_path,
            "source_date": occurred_at.isoformat() if occurred_at else None,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "content_hash": chunk.content_hash,
        })
    return payloads



@app.command("payload-indexes")
def payload_indexes(
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    """Inspect or create Qdrant payload indexes used by search filters."""
    settings = get_settings()
    client = QdrantClient(
        url=settings.qdrant_url,
        timeout=600,
    )
    if not client.collection_exists(
        settings.qdrant_collection
    ):
        _json({
            "ok": False,
            "collection": settings.qdrant_collection,
            "collection_exists": False,
        })
        raise typer.Exit(2)

    required = {
        "source_type": models.PayloadSchemaType.KEYWORD,
    }
    before_info = client.get_collection(
        settings.qdrant_collection
    )
    before_schema = (
        getattr(before_info, "payload_schema", None)
        or {}
    )
    existing_before = sorted(
        str(name)
        for name in before_schema.keys()
    )
    missing_before = sorted(
        name
        for name in required
        if name not in before_schema
    )

    if not apply:
        _json({
            "ok": not missing_before,
            "apply": False,
            "collection": settings.qdrant_collection,
            "required_indexes": sorted(required),
            "existing_indexes": existing_before,
            "missing_indexes": missing_before,
            "note": (
                "Dry run only. Repeat with --apply "
                "to create missing payload indexes."
            ),
        })
        return

    created: list[str] = []
    for field_name in missing_before:
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=field_name,
            field_schema=required[field_name],
            wait=True,
        )
        created.append(field_name)

    after_info = client.get_collection(
        settings.qdrant_collection
    )
    after_schema = (
        getattr(after_info, "payload_schema", None)
        or {}
    )
    missing_after = sorted(
        name
        for name in required
        if name not in after_schema
    )
    _json({
        "ok": not missing_after,
        "apply": True,
        "collection": settings.qdrant_collection,
        "created_indexes": created,
        "existing_indexes": sorted(
            str(name)
            for name in after_schema.keys()
        ),
        "missing_indexes": missing_after,
    })
    if missing_after:
        raise typer.Exit(1)

@app.command("rebuild")
def rebuild(
    apply: bool = typer.Option(False, "--apply"),
    reset_collection: bool = typer.Option(False, "--reset-collection"),
    confirmation: str = typer.Option("", "--confirmation"),
    batch_size: int = typer.Option(256, min=16, max=1024),
    source_type: str = typer.Option("all"),
    max_chunks: int = typer.Option(0, min=0),
) -> None:
    clean_source = source_type.strip().lower()
    if clean_source not in {"all", "email", "attachment", "document"}:
        raise typer.BadParameter(
            "source-type must be all, email, attachment, or document"
        )
    settings = get_settings()
    with SessionLocal() as session:
        conditions = [
            DocumentChunk.embedding_status == "indexed",
            DocumentChunk.qdrant_point_id.is_not(None),
        ]
        if clean_source != "all":
            conditions.append(DocumentChunk.source_type == clean_source)
        total = int(
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(*conditions)
            )
            or 0
        )
    planned = min(total, max_chunks) if max_chunks else total
    plan = {
        "apply": apply,
        "reset_collection": reset_collection,
        "source_type": clean_source,
        "database_chunks": total,
        "planned_chunks": planned,
        "batch_size": batch_size,
        "embedding_model": settings.embedding_model,
    }
    if not apply:
        plan["note"] = (
            "Dry run only. Use --apply to upsert vectors. "
            "Reset requires --confirmation DROP_WORKMIND_QDRANT."
        )
        _json(plan)
        return
    if reset_collection and clean_source != "all":
        raise typer.BadParameter(
            "Collection reset is allowed only with --source-type all"
        )
    if reset_collection and max_chunks:
        raise typer.BadParameter(
            "Collection reset cannot be combined with --max-chunks"
        )
    if reset_collection and confirmation != "DROP_WORKMIND_QDRANT":
        raise typer.BadParameter(
            "Reset requires --confirmation DROP_WORKMIND_QDRANT"
        )

    client = get_qdrant_client()
    if reset_collection and client.collection_exists(settings.qdrant_collection):
        client.delete_collection(settings.qdrant_collection)

    processed = 0
    last_id: uuid.UUID | None = None
    while processed < planned:
        take = min(batch_size, planned - processed)
        with SessionLocal() as session:
            conditions = [
                DocumentChunk.embedding_status == "indexed",
                DocumentChunk.qdrant_point_id.is_not(None),
            ]
            if clean_source != "all":
                conditions.append(DocumentChunk.source_type == clean_source)
            if last_id is not None:
                conditions.append(DocumentChunk.id > last_id)
            chunks = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(*conditions)
                    .order_by(DocumentChunk.id)
                    .limit(take)
                )
            )
        if not chunks:
            break
        vectors = embed_texts([chunk.text for chunk in chunks])
        ensure_collection(client, vector_size=len(vectors[0]))
        payloads = _payloads_for_batch(chunks)
        upsert_chunks(
            client,
            point_ids=[chunk.qdrant_point_id for chunk in chunks],
            vectors=vectors,
            payloads=payloads,
        )
        processed += len(chunks)
        last_id = chunks[-1].id
        typer.echo(json.dumps({
            "processed": processed,
            "planned": planned,
            "last_chunk_id": str(last_id),
        }, ensure_ascii=False))

    _json({**plan, "completed_chunks": processed, "status": "completed"})


if __name__ == "__main__":
    app()
