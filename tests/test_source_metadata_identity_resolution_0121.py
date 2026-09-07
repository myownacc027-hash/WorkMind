from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from packages.common.config import get_settings
from packages.database.models import (
    Attachment,
    Base,
    DocumentChunk,
    EmailMessage,
    EmailThread,
    ExternalDocument,
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvent,
    KnowledgeEvidence,
    Mailbox,
)
from packages.knowledge.extraction import _allowed_source_metadata_evidence
from packages.knowledge.identity_resolution import resolve_identity_candidates


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: Any, **_kwargs: Any) -> str:
    return "JSON"


TABLES = [
    Mailbox.__table__,
    EmailThread.__table__,
    EmailMessage.__table__,
    Attachment.__table__,
    ExternalDocument.__table__,
    DocumentChunk.__table__,
    KnowledgeEntity.__table__,
    KnowledgeClaim.__table__,
    KnowledgeEvent.__table__,
    KnowledgeEvidence.__table__,
]


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=TABLES)
    factory = sessionmaker(engine, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed_mail(session: Session) -> tuple[EmailMessage, Attachment]:
    mailbox = Mailbox(id=uuid.uuid4(), name="Corporate Outlook")
    thread = EmailThread(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        conversation_id="metadata-identity",
        message_count=1,
    )
    message = EmailMessage(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        thread_id=thread.id,
        outlook_entry_id="metadata-identity-entry",
        conversation_id="metadata-identity",
        folder_path="Inbox",
        subject="RE: Обработка",
        received_at=datetime(2026, 8, 13, 17, 37, tzinfo=UTC),
        body_text="Внес изменения.",
        content_hash="e" * 64,
        has_attachments=True,
        processing_status="indexed",
    )
    attachment = Attachment(
        id=uuid.uuid4(),
        message_id=message.id,
        filename="МИ_СоздатьУпаковкуДляНоменклатурыV2.epf",
        content_hash="a" * 64,
        size_bytes=16360,
        mime_type="application/octet-stream",
        processing_status="unsupported",
        metadata_json={},
    )
    session.add_all([mailbox, thread, message, attachment])
    session.commit()
    return message, attachment


def test_thread_attachment_metadata_is_exposed_as_bounded_identity_scope(session) -> None:
    message, attachment = _seed_mail(session)
    rows = _allowed_source_metadata_evidence(
        session,
        [("email", message.id)],
    )
    assert rows == [
        {
            "source_ref": f"workmind://attachment/{attachment.id}",
            "source_type": "attachment",
            "metadata": {
                "filename": "МИ_СоздатьУпаковкуДляНоменклатурыV2.epf",
                "content_hash": "a" * 64,
                "size_bytes": 16360,
                "mime_type": "application/octet-stream",
                "parent_source_ref": f"workmind://email/{message.id}",
            },
        }
    ]


def test_verified_filename_can_drive_exact_existing_entity_resolution(session) -> None:
    filename = "МИ_СоздатьУпаковкуДляНоменклатурыV2.epf"
    entity = KnowledgeEntity(
        id=uuid.uuid4(),
        entity_type="system",
        canonical_name="МИ_СоздатьУпаковкуДляНоменклатурыV2",
        normalized_key="ми создатьупаковкудляноменклатурыv2 epf",
        external_id=filename,
        status="active",
        attributes_json={},
    )
    session.add(entity)
    session.commit()
    settings = get_settings().model_copy(
        update={
            "knowledge_identity_max_candidates": 8,
            "knowledge_identity_exact_query_limit": 60,
            "knowledge_identity_exact_phrase_limit": 600,
            "knowledge_identity_lexical_scan_limit": 300,
            "knowledge_identity_lexical_min_score": 0.62,
            "knowledge_identity_semantic_source_limit": 20,
            "knowledge_identity_semantic_threshold": 0.58,
            "knowledge_identity_semantic_chunk_limit": 60,
            "knowledge_identity_semantic_evidence_limit": 240,
            "knowledge_identity_semantic_entity_limit": 60,
            "knowledge_identity_active_claims_per_entity": 10,
            "knowledge_identity_mandatory_claims_safety_cap": 200,
            "knowledge_identity_recent_claims_per_entity": 10,
            "knowledge_identity_recent_events_per_entity": 5,
        }
    )
    result = resolve_identity_candidates(
        session,
        [filename],
        settings=settings,
        semantic_search=lambda *_args, **_kwargs: {"results": [], "warnings": []},
    )
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["entity_id"] == str(entity.id)
    assert "exact_external_id" in candidate["identity_match"]["methods"]
    assert candidate["identity_match"]["score"] == 1.0
