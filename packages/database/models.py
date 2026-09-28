from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.database.base import Base


class Mailbox(Base):
    __tablename__ = "mailboxes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    outlook_store_id: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list[EmailMessage]] = relationship(
        back_populates="mailbox"
    )


class EmailThread(Base):
    __tablename__ = "email_threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailboxes.id"),
        nullable=False,
    )
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    subject_normalized: Mapped[str | None] = mapped_column(Text)
    first_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    message_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("mailbox_id", "conversation_id"),
    )


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailboxes.id"),
        nullable=False,
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("email_threads.id")
    )
    outlook_entry_id: Mapped[str] = mapped_column(Text, nullable=False)
    outlook_store_id: Mapped[str | None] = mapped_column(Text)
    internet_message_id: Mapped[str | None] = mapped_column(Text)
    conversation_id: Mapped[str | None] = mapped_column(Text)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    sender_name: Mapped[str | None] = mapped_column(Text)
    sender_email: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    body_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    body_html: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(80))
    raw_json_path: Mapped[str | None] = mapped_column(Text)
    has_attachments: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    processing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="new",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    mailbox: Mapped[Mailbox] = relationship(
        back_populates="messages"
    )
    thread: Mapped[EmailThread | None] = relationship()
    recipients: Mapped[list[EmailRecipient]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )
    locations: Mapped[list[EmailLocation]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "mailbox_id",
            "outlook_entry_id",
            name="uq_message_entry",
        ),
        Index(
            "ix_email_messages_internet_message_id",
            "internet_message_id",
        ),
        Index("ix_email_messages_received_at", "received_at"),
        Index("ix_email_messages_sent_at", "sent_at"),
        Index("ix_email_messages_content_hash", "content_hash"),
        Index("ix_email_messages_dedupe_key", "dedupe_key"),
    )


class EmailLocation(Base):
    __tablename__ = "email_locations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    outlook_store_id: Mapped[str] = mapped_column(Text, nullable=False)
    store_display_name: Mapped[str] = mapped_column(Text, nullable=False)
    outlook_entry_id: Mapped[str] = mapped_column(Text, nullable=False)
    folder_entry_id: Mapped[str | None] = mapped_column(Text)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="primary",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    message: Mapped[EmailMessage] = relationship(
        back_populates="locations"
    )

    __table_args__ = (
        UniqueConstraint(
            "outlook_store_id",
            "outlook_entry_id",
            name="uq_email_location_store_entry",
        ),
        Index("ix_email_locations_message", "message_id"),
        Index(
            "ix_email_locations_store_folder",
            "outlook_store_id",
            "folder_entry_id",
        ),
    )


class EmailRecipient(Base):
    __tablename__ = "email_recipients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_type: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)

    message: Mapped[EmailMessage] = relationship(
        back_populates="recipients"
    )

class StoredObject(Base):
    __tablename__ = "stored_objects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="stored_object"
    )

    __table_args__ = (
        UniqueConstraint(
            "sha256",
            name="uq_stored_objects_sha256",
        ),
        UniqueConstraint(
            "storage_path",
            name="uq_stored_objects_storage_path",
        ),
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    stored_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "stored_objects.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    archive_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    processing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="new",
    )
    extracted_text_path: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str | None] = mapped_column(String(64))
    extracted_chars: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    sheet_count: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    message: Mapped[EmailMessage] = relationship(
        back_populates="attachments"
    )
    stored_object: Mapped[StoredObject | None] = relationship(
        back_populates="attachments"
    )


class ExternalDocument(Base):
    __tablename__ = "external_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    original_filename: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    source_path: Mapped[str | None] = mapped_column(Text)
    archive_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    processed_text_path: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    source_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    processing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="processing",
    )
    extraction_method: Mapped[str | None] = mapped_column(String(64))
    extracted_chars: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    sheet_count: Mapped[int | None] = mapped_column(Integer)
    slide_count: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "content_hash",
            name="uq_external_documents_content_hash",
        ),
        Index(
            "ix_external_documents_processing_status",
            "processing_status",
        ),
        Index(
            "ix_external_documents_source_date",
            "source_date",
        ),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(255)
    )
    embedding_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "chunk_index",
        ),
        Index(
            "ix_document_chunks_source",
            "source_type",
            "source_id",
        ),
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailboxes.id"),
        nullable=False,
    )
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_item_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("mailbox_id", "folder_path"),
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AgentActionLog(Base):
    __tablename__ = "agent_action_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tool_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    transport: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="stdio",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    duration_ms: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    result_count: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(255))
    request_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    response_summary_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_agent_action_logs_created_at", "created_at"),
        Index(
            "ix_agent_action_logs_tool_status",
            "tool_name",
            "status",
        ),
    )



class KnowledgeEntity(Base):
    __tablename__ = "knowledge_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    description: Mapped[str | None] = mapped_column(Text)
    attributes_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "normalized_key",
            name="uq_knowledge_entity_type_key",
        ),
        Index("ix_knowledge_entity_external_id", "external_id"),
        Index(
            "ix_knowledge_entity_type_status",
            "entity_type",
            "status",
        ),
    )


class KnowledgeClaim(Base):
    __tablename__ = "knowledge_claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    predicate: Mapped[str] = mapped_column(String(128), nullable=False)
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="SET NULL")
    )
    object_text: Mapped[str | None] = mapped_column(Text)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    claim_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "knowledge_claims.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_knowledge_claim_supersedes",
        )
    )
    superseded_by_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "knowledge_claims.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_knowledge_claim_superseded_by",
        )
    )
    attributes_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="mcp",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "object_entity_id IS NOT NULL OR object_text IS NOT NULL",
            name="ck_knowledge_claim_has_object",
        ),
        UniqueConstraint("claim_hash", name="uq_knowledge_claim_hash"),
        UniqueConstraint("claim_key", name="uq_knowledge_claim_key"),
        Index(
            "ix_knowledge_claim_subject_predicate",
            "subject_entity_id",
            "predicate",
        ),
        Index("ix_knowledge_claim_object_entity", "object_entity_id"),
        Index("ix_knowledge_claim_status", "status"),
        Index("ix_knowledge_claim_supersedes", "supersedes_claim_id"),
        Index("ix_knowledge_claim_superseded_by", "superseded_by_claim_id"),
    )


class KnowledgeEvent(Base):
    __tablename__ = "knowledge_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="confirmed",
    )
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attributes_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="mcp",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("event_hash", name="uq_knowledge_event_hash"),
        Index(
            "ix_knowledge_event_entity_date",
            "entity_id",
            "occurred_at",
        ),
    )


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE")
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_claims.id", ondelete="CASCADE")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_events.id", ondelete="CASCADE")
    )
    evidence_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="chunk",
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(32), nullable=False)
    source_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "(entity_id IS NOT NULL AND claim_id IS NULL AND event_id IS NULL) OR "
            "(entity_id IS NULL AND claim_id IS NOT NULL AND event_id IS NULL) OR "
            "(entity_id IS NULL AND claim_id IS NULL AND event_id IS NOT NULL)",
            name="ck_knowledge_evidence_one_parent",
        ),
        CheckConstraint(
            "(evidence_kind = 'chunk' AND chunk_id IS NOT NULL) OR "
            "(evidence_kind = 'source_metadata' AND chunk_id IS NULL)",
            name="ck_knowledge_evidence_kind_payload",
        ),
        UniqueConstraint(
            "evidence_hash",
            name="uq_knowledge_evidence_hash",
        ),
        Index(
            "ix_knowledge_evidence_source",
            "source_type",
            "source_id",
        ),
        Index("ix_knowledge_evidence_entity", "entity_id"),
        Index("ix_knowledge_evidence_chunk", "chunk_id"),
    )

class B24ImportSnapshot(Base):
    __tablename__ = "b24_import_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    transport: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
    )

    source_name: Mapped[str | None] = mapped_column(
        sa.Text,
    )

    source_hash: Mapped[str | None] = mapped_column(
        sa.String(64),
    )

    source_timezone: Mapped[str] = mapped_column(
        sa.String(128),
        nullable=False,
    )

    present_columns: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    row_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
    )

    task_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
    )

    skipped_row_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    imported_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "transport",
            "source_hash",
            name="uq_b24_import_snapshots_transport_hash",
        ),
        sa.Index(
            "ix_b24_import_snapshots_imported_at",
            "imported_at",
        ),
    )


class B24Task(Base):
    __tablename__ = "b24_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    external_id: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    last_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "b24_import_snapshots.id",
            ondelete="SET NULL",
        ),
    )

    transport: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
    )

    source_timezone: Mapped[str] = mapped_column(
        sa.String(128),
        nullable=False,
    )

    source_url: Mapped[str | None] = mapped_column(sa.Text)

    project_external_id: Mapped[str | None] = mapped_column(
        sa.String(64)
    )

    present_columns: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    raw_row: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    raw_hyperlinks: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    raw_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    canonical_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    extraction_input_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)

    last_activity_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    deadline_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    created_at_source: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    updated_at_source: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    creator_label: Mapped[str | None] = mapped_column(sa.Text)
    assignee_label: Mapped[str | None] = mapped_column(sa.Text)

    coassignee_labels: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    observer_labels: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    additional_employee_labels: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    status: Mapped[str | None] = mapped_column(sa.Text)
    stage: Mapped[str | None] = mapped_column(sa.Text)
    project_name: Mapped[str | None] = mapped_column(sa.Text)

    parent_external_id: Mapped[str | None] = mapped_column(
        sa.String(64)
    )

    parent_title_snapshot: Mapped[str | None] = mapped_column(
        sa.Text
    )

    parent_stage_snapshot: Mapped[str | None] = mapped_column(
        sa.Text
    )

    planned_duration_minutes: Mapped[int | None] = mapped_column(
        sa.Integer
    )

    spent_minutes: Mapped[int | None] = mapped_column(
        sa.Integer
    )

    track_work_time: Mapped[bool | None] = mapped_column(
        sa.Boolean
    )

    tags: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    business_direction: Mapped[str | None] = mapped_column(
        sa.Text
    )

    request_type: Mapped[str | None] = mapped_column(sa.Text)
    task_kind: Mapped[str | None] = mapped_column(sa.Text)
    product: Mapped[str | None] = mapped_column(sa.Text)

    planned_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    planned_release_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    actual_release_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )

    deadline_change_in_progress: Mapped[bool | None] = mapped_column(
        sa.Boolean
    )

    deadline_change_count: Mapped[int | None] = mapped_column(
        sa.Integer
    )

    ai_used: Mapped[bool | None] = mapped_column(sa.Boolean)

    helpdesk_number: Mapped[str | None] = mapped_column(sa.Text)

    progress_percent: Mapped[float | None] = mapped_column(
        sa.Float
    )

    crm_context_raw: Mapped[str | None] = mapped_column(sa.Text)
    flow_raw: Mapped[str | None] = mapped_column(sa.Text)
    priority_raw: Mapped[str | None] = mapped_column(sa.Text)

    deadline_change_request_raw: Mapped[str | None] = mapped_column(
        sa.Text
    )

    deadline_change_reason: Mapped[str | None] = mapped_column(
        sa.Text
    )

    approval_comment: Mapped[str | None] = mapped_column(sa.Text)
    approval_result: Mapped[str | None] = mapped_column(sa.Text)

    first_imported_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "external_id",
            name="uq_b24_tasks_external_id",
        ),
        sa.Index(
            "ix_b24_tasks_status",
            "status",
        ),
        sa.Index(
            "ix_b24_tasks_parent_external_id",
            "parent_external_id",
        ),
        sa.Index(
            "ix_b24_tasks_project_external_id",
            "project_external_id",
        ),
        sa.Index(
            "ix_b24_tasks_extraction_input_hash",
            "extraction_input_hash",
        ),
    )


class B24TaskObservation(Base):
    __tablename__ = "b24_task_observations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "b24_tasks.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "b24_import_snapshots.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    external_id: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    transport: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
    )

    source_timezone: Mapped[str] = mapped_column(
        sa.String(128),
        nullable=False,
    )

    present_columns: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    raw_row: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    raw_hyperlinks: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    canonical_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    extraction_input_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    raw_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    canonical_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    extraction_input_hash: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
    )

    contract_version: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        default="b24-source-v1",
    )

    normalizer_version: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        default="b24-normalizer-v1",
    )

    observed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "snapshot_id",
            "external_id",
            name="uq_b24_task_observations_snapshot_external",
        ),
        sa.Index(
            "ix_b24_task_observations_task",
            "task_id",
            "observed_at",
        ),
        sa.Index(
            "ix_b24_task_observations_extraction_hash",
            "extraction_input_hash",
        ),
    )


class KnowledgeExtractionJob(Base):
    __tablename__ = "knowledge_extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    source_content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    extractor_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    leased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    bundle_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    validation_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    apply_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "source_content_hash",
            "extractor_version",
            name="uq_knowledge_extraction_source_version",
        ),
        Index(
            "ix_knowledge_extraction_status_available",
            "status",
            "available_at",
            "priority",
        ),
        Index(
            "ix_knowledge_extraction_source",
            "source_type",
            "source_id",
        ),
        Index(
            "ix_knowledge_extraction_lease",
            "status",
            "lease_expires_at",
        ),
    )


class HistoryImportState(Base):
    __tablename__ = "history_import_states"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailboxes.id"),
        nullable=False,
    )
    outlook_store_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    store_display_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    folder_entry_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    folder_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    date_attr: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    cursor_entry_id: Mapped[str | None] = mapped_column(Text)
    cursor_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    oldest_allowed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    seen_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    deduplicated_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    unchanged_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "outlook_store_id",
            "folder_entry_id",
            name="uq_history_state_store_folder",
        ),
        Index("ix_history_import_state_status", "status"),
    )
