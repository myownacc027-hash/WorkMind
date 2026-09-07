from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
from packages.knowledge import service


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
def factory(monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=TABLES)
    result = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", result)
    try:
        yield result
    finally:
        engine.dispose()


def _seed(factory: sessionmaker[Session]) -> dict[str, Any]:
    now = datetime(2026, 8, 13, 17, 37, tzinfo=UTC)
    mailbox = Mailbox(id=uuid.uuid4(), name="Corporate Outlook")
    thread = EmailThread(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        conversation_id="conversation-0121",
        message_count=1,
    )
    message = EmailMessage(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        thread_id=thread.id,
        outlook_entry_id="entry-0121",
        conversation_id="conversation-0121",
        folder_path="Inbox",
        subject="RE: Обработка",
        sender_name="Андреев Артем Андреевич",
        sender_email="sender@example.com",
        received_at=now,
        body_text=(
            "Внес изменения. Дата актуальности проставляется автоматически "
            "датой создания упаковки."
        ),
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
    chunk = DocumentChunk(
        id=uuid.uuid4(),
        source_type="email",
        source_id=message.id,
        chunk_index=0,
        text=message.body_text,
        content_hash="c" * 64,
        embedding_status="indexed",
        metadata_json={},
    )
    with factory() as session:
        session.add_all([mailbox, thread, message, attachment, chunk])
        session.commit()
    return {
        "message": message,
        "attachment": attachment,
        "chunk": chunk,
    }


def _metadata_row(seed: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    attachment: Attachment = seed["attachment"]
    metadata = {
        "filename": attachment.filename,
        "content_hash": attachment.content_hash,
        "size_bytes": attachment.size_bytes,
        "mime_type": attachment.mime_type,
        "parent_source_ref": f"workmind://email/{attachment.message_id}",
    }
    metadata.update(overrides)
    return {
        "evidence_kind": "source_metadata",
        "source_ref": f"workmind://attachment/{attachment.id}",
        "metadata": metadata,
        "evidence_role": "direct_statement",
    }


def test_metadata_identity_evidence_validates_without_fake_chunk(factory) -> None:
    seed = _seed(factory)
    result = service._validated_metadata_evidence(
        factory(),
        _metadata_row(seed),
    )
    assert result["evidence_kind"] == "source_metadata"
    assert result["chunk_id"] is None
    assert result["metadata_json"]["filename"].endswith("V2.epf")

    with factory() as session:
        attachment: Attachment = seed["attachment"]
        fake = list(
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.source_type == "attachment",
                    DocumentChunk.source_id == attachment.id,
                )
            )
        )
        assert fake == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"filename": "evil.epf"}, "mismatch for filename"),
        ({"content_hash": "0" * 64}, "mismatch for content_hash"),
        ({"arbitrary_assertion": "WMS"}, "Unsupported source_metadata fields"),
    ],
)
def test_metadata_tampering_is_rejected(factory, overrides, expected) -> None:
    seed = _seed(factory)
    with factory() as session:
        with pytest.raises(service.KnowledgeValidationError, match=expected):
            service._validated_metadata_evidence(
                session,
                _metadata_row(seed, **overrides),
            )


def test_bundle_saves_entity_metadata_and_claim_chunk_evidence(factory) -> None:
    seed = _seed(factory)
    message: EmailMessage = seed["message"]
    chunk: DocumentChunk = seed["chunk"]
    bundle = {
        "entities": [
            {
                "key": "system_mi_create_package_v2",
                "entity_type": "system",
                "canonical_name": "МИ_СоздатьУпаковкуДляНоменклатурыV2",
                "external_id": "МИ_СоздатьУпаковкуДляНоменклатурыV2.epf",
                "identity_evidence": [_metadata_row(seed)],
            }
        ],
        "claims": [
            {
                "subject_key": "system_mi_create_package_v2",
                "predicate": "actuality_date_rule",
                "object_text": (
                    "Дата актуальности автоматически устанавливается равной "
                    "дате создания упаковки"
                ),
                "claim_text": (
                    "В обработке дата актуальности автоматически устанавливается "
                    "равной дате создания упаковки."
                ),
                "evidence_type": "direct_statement",
                "confidence": 0.98,
                "observed_at": message.received_at.isoformat(),
                "evidence": [
                    {
                        "source_ref": f"workmind://email/{message.id}",
                        "chunk_id": str(chunk.id),
                        "evidence_role": "direct_statement",
                        "excerpt": message.body_text,
                    }
                ],
            }
        ],
        "relations": [],
        "events": [],
    }
    result = service.import_knowledge_bundle(
        json.dumps(bundle, ensure_ascii=False),
        apply=True,
        created_by="test",
    )
    assert result["ok"] is True
    assert result["apply"] is True
    assert result["created"]["entities"] == 1
    assert result["created"]["claims"] == 1
    assert result["created"]["evidence"] == 2

    with factory() as session:
        entity = session.scalar(select(KnowledgeEntity))
        claim = session.scalar(select(KnowledgeClaim))
        rows = list(session.scalars(select(KnowledgeEvidence)))
        assert entity is not None and claim is not None
        assert len(rows) == 2
        identity = next(row for row in rows if row.entity_id == entity.id)
        factual = next(row for row in rows if row.claim_id == claim.id)
        assert identity.evidence_kind == "source_metadata"
        assert identity.chunk_id is None
        assert identity.metadata_json["filename"].endswith("V2.epf")
        assert factual.evidence_kind == "chunk"
        assert factual.chunk_id == chunk.id
        assert factual.metadata_json == {}


def test_functional_claim_cannot_use_metadata_only_evidence(factory) -> None:
    seed = _seed(factory)
    bundle = {
        "entities": [
            {
                "key": "system_mi_create_package_v2",
                "entity_type": "system",
                "canonical_name": "МИ_СоздатьУпаковкуДляНоменклатурыV2",
                "external_id": "МИ_СоздатьУпаковкуДляНоменклатурыV2.epf",
                "identity_evidence": [_metadata_row(seed)],
            }
        ],
        "claims": [
            {
                "subject_key": "system_mi_create_package_v2",
                "predicate": "actuality_date_rule",
                "object_text": "rule",
                "claim_text": "rule",
                "evidence_type": "direct_statement",
                "evidence": [_metadata_row(seed)],
            }
        ],
        "relations": [],
        "events": [],
    }
    result = service.import_knowledge_bundle(
        json.dumps(bundle, ensure_ascii=False),
        apply=False,
    )
    assert result["ok"] is False
    assert any("chunk_id" in error for error in result["errors"])


def test_entity_identity_evidence_is_readable(factory) -> None:
    seed = _seed(factory)
    bundle = {
        "entities": [
            {
                "key": "system_mi_create_package_v2",
                "entity_type": "system",
                "canonical_name": "МИ_СоздатьУпаковкуДляНоменклатурыV2",
                "external_id": "МИ_СоздатьУпаковкуДляНоменклатурыV2.epf",
                "identity_evidence": [_metadata_row(seed)],
            }
        ],
        "claims": [],
        "relations": [],
        "events": [],
    }
    applied = service.import_knowledge_bundle(
        json.dumps(bundle, ensure_ascii=False),
        apply=True,
        created_by="test",
    )
    assert applied["ok"] is True
    entity_id = applied["entities"][0]["entity_id"]
    result = service.get_entity(
        entity_id,
        include_claims=False,
        include_relations=False,
        include_timeline=False,
        include_evidence=True,
        include_excerpt=True,
    )
    assert result["identity_evidence_count"] == 1
    assert len(result["identity_evidence"]) == 1
    evidence = result["identity_evidence"][0]
    assert evidence["evidence_kind"] == "source_metadata"
    assert evidence["chunk_id"] is None
    assert evidence["metadata"]["filename"].endswith("V2.epf")
