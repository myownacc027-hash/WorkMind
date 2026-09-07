from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.common.config import Settings
from packages.database.models import DocumentChunk, ExternalDocument
from packages.processing.attachments import extract_document
from packages.processing.chunking import build_external_document, chunk_text
from packages.processing.embeddings import embed_texts

_TEMP_PREFIXES = ("~$", ".~lock.")
_TEMP_SUFFIXES = (".tmp", ".part", ".crdownload")


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:220] or "document"


def discover_files(import_dir: Path, *, recursive: bool = True) -> list[Path]:
    iterator = import_dir.rglob("*") if recursive else import_dir.glob("*")
    result: list[Path] = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.name.startswith(_TEMP_PREFIXES):
            continue
        if path.suffix.casefold() in _TEMP_SUFFIXES:
            continue
        result.append(path)
    return sorted(result, key=lambda item: str(item).casefold())


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _unique_destination(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / safe_filename(filename)
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for number in range(1, 10_000):
        alternative = directory / f"{stem}_{number}{suffix}"
        if not alternative.exists():
            return alternative
    raise RuntimeError(f"Could not allocate destination for {filename}")


def _move_out_of_import(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        return Path(shutil.move(str(source), str(destination)))
    except OSError:
        shutil.copy2(source, destination)
        source.unlink()
        return destination


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _batched(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def document_payload(document: ExternalDocument, chunk: DocumentChunk) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.id),
        "source_type": "document",
        "source_id": str(document.id),
        "source_ref": f"workmind://document/{document.id}",
        "filename": document.original_filename,
        "mime_type": document.mime_type,
        "source_date": (
            document.source_date.isoformat() if document.source_date else None
        ),
        "archive_path": document.archive_path,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "content_hash": chunk.content_hash,
        "document_content_hash": document.content_hash,
    }


def _delete_existing_chunks(session: Session, document_id: uuid.UUID) -> list[uuid.UUID]:
    existing = list(
        session.scalars(
            select(DocumentChunk).where(
                DocumentChunk.source_type == "document",
                DocumentChunk.source_id == document_id,
            )
        )
    )
    point_ids = [item.qdrant_point_id for item in existing if item.qdrant_point_id]
    session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.source_type == "document",
            DocumentChunk.source_id == document_id,
        )
    )
    return point_ids


def process_external_document(
    session: Session,
    *,
    document: ExternalDocument,
    settings: Settings,
) -> dict[str, Any]:
    archive_path = Path(document.archive_path)
    document.processing_status = "processing"
    document.last_error = None

    try:
        result = extract_document(
            archive_path,
            max_bytes=settings.document_max_bytes,
            max_chars=settings.document_max_extracted_chars,
            supported_extensions=settings.document_supported_extensions,
        )
    except Exception as exc:
        session.rollback()
        persisted = session.get(ExternalDocument, document.id)
        if persisted is not None:
            persisted.processing_status = "error"
            persisted.last_error = (
                f"Extraction failed: {type(exc).__name__}: {exc}"
            )
            persisted.processed_at = datetime.now(UTC)
            session.commit()
        raise

    old_points = _delete_existing_chunks(session, document.id)
    session.flush()
    from packages.search.indexer import delete_points, upsert_chunks
    from packages.search.qdrant import get_qdrant_client

    qdrant = get_qdrant_client()
    delete_points(qdrant, old_points)

    document.extraction_method = result.method
    document.page_count = result.metadata.get("page_count")
    document.sheet_count = result.metadata.get("sheet_count")
    document.slide_count = result.metadata.get("slide_count")
    document.metadata_json = {
        **(document.metadata_json or {}),
        **result.metadata,
    }
    document.processed_at = datetime.now(UTC)

    if result.status != "extracted":
        document.processing_status = result.status
        document.last_error = result.error
        document.extracted_chars = 0
        document.processed_text_path = None
        session.commit()
        return {
            "status": result.status,
            "document_id": str(document.id),
            "source_ref": f"workmind://document/{document.id}",
            "chunks": 0,
            "error": result.error,
        }

    text_path = settings.document_text_dir / f"{document.id}.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(result.text, encoding="utf-8")

    source_date = document.source_date.isoformat() if document.source_date else None
    normalized_document = build_external_document(
        filename=document.original_filename,
        source_date=source_date,
        mime_type=document.mime_type,
        extracted_text=result.text,
    )
    chunks = chunk_text(
        normalized_document,
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )

    rows: list[DocumentChunk] = []
    for chunk in chunks:
        chunk_hash = hashlib.sha256(
            chunk.text.encode("utf-8", errors="replace")
        ).hexdigest()
        row = DocumentChunk(
            source_type="document",
            source_id=document.id,
            chunk_index=chunk.index,
            text=chunk.text,
            content_hash=chunk_hash,
            qdrant_point_id=uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"workmind:document:{document.id}:{chunk.index}",
            ),
            embedding_model=settings.embedding_model,
            embedding_status="processing",
            metadata_json={
                "filename": document.original_filename,
                "mime_type": document.mime_type,
                "document_content_hash": document.content_hash,
            },
        )
        session.add(row)
        rows.append(row)

    document.processed_text_path = str(text_path)
    document.extracted_chars = len(result.text)
    session.flush()
    session.commit()

    point_ids = [row.qdrant_point_id for row in rows if row.qdrant_point_id]
    try:
        vectors: list[list[float]] = []
        for batch in _batched([row.text for row in rows], 256):
            vectors.extend(embed_texts(batch))
        payloads = [document_payload(document, row) for row in rows]
        for start in range(0, len(rows), 512):
            end = start + 512
            upsert_chunks(
                qdrant,
                point_ids=point_ids[start:end],
                vectors=vectors[start:end],
                payloads=payloads[start:end],
            )
    except Exception as exc:
        try:
            delete_points(qdrant, point_ids)
        except Exception:
            pass
        with session.begin():
            persisted = session.get(ExternalDocument, document.id)
            if persisted is not None:
                persisted.processing_status = "error"
                persisted.last_error = f"Indexing failed: {type(exc).__name__}: {exc}"
                persisted.processed_at = datetime.now(UTC)
            for row in session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.source_type == "document",
                    DocumentChunk.source_id == document.id,
                )
            ):
                row.embedding_status = "error"
        raise

    with session.begin():
        persisted = session.get(ExternalDocument, document.id)
        if persisted is None:
            raise LookupError(f"External document disappeared: {document.id}")
        persisted.processing_status = "indexed"
        persisted.last_error = None
        persisted.processed_at = datetime.now(UTC)
        for row in session.scalars(
            select(DocumentChunk).where(
                DocumentChunk.source_type == "document",
                DocumentChunk.source_id == document.id,
            )
        ):
            row.embedding_status = "indexed"
        if settings.knowledge_extraction_auto_queue:
            from packages.knowledge.extraction import try_enqueue_indexed_source

            try_enqueue_indexed_source(
                session,
                source_type="document",
                source_id=document.id,
                settings=settings,
                reason="document_indexed",
            )

    return {
        "status": "indexed",
        "document_id": str(document.id),
        "source_ref": f"workmind://document/{document.id}",
        "chunks": len(rows),
        "extracted_chars": len(result.text),
        "extraction_method": result.method,
    }


def import_one_file(
    session: Session,
    *,
    source: Path,
    settings: Settings,
) -> dict[str, Any]:
    source = source.resolve()
    stat = source.stat()
    content_hash = sha256_file(source)
    existing = session.scalar(
        select(ExternalDocument).where(
            ExternalDocument.content_hash == content_hash
        )
    )
    if existing is not None:
        destination = _unique_destination(
            settings.document_duplicate_dir,
            f"{content_hash[:12]}_{source.name}",
        )
        moved = _move_out_of_import(source, destination)
        return {
            "status": "duplicate",
            "filename": source.name,
            "content_hash": content_hash,
            "existing_document_id": str(existing.id),
            "existing_source_ref": f"workmind://document/{existing.id}",
            "moved_to": str(moved),
        }

    document_id = uuid.uuid4()
    imported_at = datetime.now(UTC)
    archive_path = (
        settings.document_archive_dir
        / f"{imported_at.year:04d}"
        / f"{imported_at.month:02d}"
        / str(document_id)
        / safe_filename(source.name)
    )
    _copy_atomic(source, archive_path)
    archived_hash = sha256_file(archive_path)
    if archived_hash != content_hash:
        archive_path.unlink(missing_ok=True)
        raise IOError(
            "Archived document hash does not match the source file."
        )

    mime_type = mimetypes.guess_type(source.name)[0]
    source_date = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    document = ExternalDocument(
        id=document_id,
        original_filename=source.name,
        source_path=str(source),
        archive_path=str(archive_path),
        mime_type=mime_type,
        content_hash=content_hash,
        size_bytes=stat.st_size,
        source_date=source_date,
        processing_status="processing",
        metadata_json={
            "imported_from": str(source),
            "archive_verified": True,
        },
    )
    session.add(document)
    session.commit()

    try:
        result = process_external_document(
            session,
            document=document,
            settings=settings,
        )
    except Exception as exc:
        session.rollback()
        persisted = session.get(ExternalDocument, document_id)
        if persisted is not None:
            persisted.processing_status = "error"
            persisted.last_error = f"{type(exc).__name__}: {exc}"
            persisted.processed_at = datetime.now(UTC)
            session.commit()
        failed_destination = _unique_destination(
            settings.document_failed_dir / "error",
            f"{document_id}_{source.name}",
        )
        moved = _move_out_of_import(source, failed_destination)
        receipt = {
            "document_id": str(document_id),
            "source_ref": f"workmind://document/{document_id}",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "failed_path": str(moved),
            "archive_path": str(archive_path),
        }
        _write_json(
            settings.document_metadata_dir / f"{document_id}.json",
            receipt,
        )
        return receipt

    if result["status"] == "indexed":
        source.unlink(missing_ok=True)
    else:
        destination = _unique_destination(
            settings.document_failed_dir / result["status"],
            f"{document_id}_{source.name}",
        )
        moved = _move_out_of_import(source, destination)
        result["failed_path"] = str(moved)

    receipt = {
        **result,
        "filename": document.original_filename,
        "content_hash": document.content_hash,
        "archive_path": document.archive_path,
        "processed_text_path": document.processed_text_path,
        "source_date": (
            document.source_date.isoformat() if document.source_date else None
        ),
    }
    _write_json(
        settings.document_metadata_dir / f"{document.id}.json",
        receipt,
    )
    return receipt
