from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailLocation,
    EmailMessage,
    EmailRecipient,
    EmailThread,
    Mailbox,
    ProcessingJob,
)


def normalize_subject(subject: str) -> str:
    value = subject.strip()
    while True:
        updated = re.sub(
            r"^(re|fw|fwd|ответ|пересл):\s*",
            "",
            value,
            flags=re.IGNORECASE,
        )
        if updated == value:
            return value.casefold()
        value = updated.strip()


def normalize_internet_message_id(
    value: str | None,
) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("<>").casefold()
    return normalized or None


def _normalize_body(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def calculate_message_hash(
    subject: str,
    sender: str | None,
    body: str,
    occurred_at: datetime | None,
) -> str:
    raw = "\n".join(
        [
            subject.strip(),
            (sender or "").strip(),
            body.strip(),
            occurred_at.isoformat() if occurred_at else "",
        ]
    )
    return hashlib.sha256(
        raw.encode("utf-8", errors="replace")
    ).hexdigest()


def calculate_dedupe_key(
    *,
    internet_message_id: str | None,
    subject: str,
    sender: str | None,
    body: str,
    occurred_at: datetime | None,
    recipients: list[dict],
) -> str:
    normalized_mid = normalize_internet_message_id(
        internet_message_id
    )
    if normalized_mid:
        digest = hashlib.sha256(
            normalized_mid.encode("utf-8")
        ).hexdigest()
        return f"mid:{digest}"

    recipient_values = sorted(
        (
            str(item.get("recipient_type") or ""),
            str(item.get("email") or "").casefold(),
            str(item.get("display_name") or "").casefold(),
        )
        for item in recipients
    )
    occurred = (
        occurred_at.astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        if occurred_at
        else ""
    )
    raw = repr(
        (
            normalize_subject(subject),
            (sender or "").strip().casefold(),
            recipient_values,
            occurred,
            _normalize_body(body),
        )
    )
    digest = hashlib.sha256(
        raw.encode("utf-8", errors="replace")
    ).hexdigest()
    return f"fp:{digest}"


def get_or_create_mailbox(
    session: Session,
    *,
    name: str,
    store_id: str | None,
) -> Mailbox:
    mailbox = session.scalar(
        select(Mailbox).where(Mailbox.name == name)
    )
    if mailbox is None:
        mailbox = Mailbox(
            name=name,
            outlook_store_id=store_id,
        )
        session.add(mailbox)
        session.flush()
    elif store_id and not mailbox.outlook_store_id:
        mailbox.outlook_store_id = store_id
    return mailbox


def get_or_create_thread(
    session: Session,
    *,
    mailbox_id: uuid.UUID,
    conversation_id: str | None,
    subject: str,
    occurred_at: datetime | None,
) -> EmailThread | None:
    if not conversation_id:
        return None
    thread = session.scalar(
        select(EmailThread).where(
            EmailThread.mailbox_id == mailbox_id,
            EmailThread.conversation_id == conversation_id,
        )
    )
    if thread is None:
        thread = EmailThread(
            mailbox_id=mailbox_id,
            conversation_id=conversation_id,
            subject_normalized=normalize_subject(subject),
            first_message_at=occurred_at,
            last_message_at=occurred_at,
            message_count=0,
        )
        session.add(thread)
        session.flush()
    else:
        if occurred_at and (
            thread.first_message_at is None
            or occurred_at < thread.first_message_at
        ):
            thread.first_message_at = occurred_at
        if occurred_at and (
            thread.last_message_at is None
            or occurred_at > thread.last_message_at
        ):
            thread.last_message_at = occurred_at
    return thread


def _message_by_entry(
    session: Session,
    *,
    mailbox_id: uuid.UUID,
    entry_id: str,
) -> EmailMessage | None:
    return session.scalar(
        select(EmailMessage).where(
            EmailMessage.mailbox_id == mailbox_id,
            EmailMessage.outlook_entry_id == entry_id,
        )
    )


def _message_by_location(
    session: Session,
    *,
    store_id: str,
    entry_id: str,
) -> EmailMessage | None:
    message_id = session.scalar(
        select(EmailLocation.message_id).where(
            EmailLocation.outlook_store_id == store_id,
            EmailLocation.outlook_entry_id == entry_id,
        )
    )
    return (
        session.get(EmailMessage, message_id)
        if message_id
        else None
    )


def _message_by_dedupe(
    session: Session,
    *,
    mailbox_id: uuid.UUID,
    dedupe_key: str,
    internet_message_id: str | None,
) -> EmailMessage | None:
    message = session.scalar(
        select(EmailMessage).where(
            EmailMessage.mailbox_id == mailbox_id,
            EmailMessage.dedupe_key == dedupe_key,
        )
    )
    if message is not None:
        return message

    normalized_mid = normalize_internet_message_id(
        internet_message_id
    )
    if not normalized_mid:
        return None

    return session.scalar(
        select(EmailMessage).where(
            EmailMessage.mailbox_id == mailbox_id,
            func.lower(
                func.replace(
                    func.replace(
                        func.trim(
                            EmailMessage.internet_message_id
                        ),
                        "<",
                        "",
                    ),
                    ">",
                    "",
                )
            )
            == normalized_mid,
        )
    )


def _merge_message_rows(
    session: Session,
    *,
    winner: EmailMessage,
    losers: list[EmailMessage],
    dedupe_key: str,
) -> list[uuid.UUID]:
    """Merge duplicate legacy rows into one stable WorkMind message."""
    loser_ids = [
        loser.id
        for loser in losers
        if loser.id != winner.id
    ]
    if not loser_ids:
        if not winner.dedupe_key:
            winner.dedupe_key = dedupe_key
        return []

    all_ids = [winner.id, *loser_ids]
    stale_point_ids = list(
        session.scalars(
            select(DocumentChunk.qdrant_point_id).where(
                DocumentChunk.source_type == "email",
                DocumentChunk.source_id.in_(all_ids),
                DocumentChunk.qdrant_point_id.is_not(None),
            )
        )
    )

    # Free the partial unique dedupe index before assigning the key to
    # the exact EntryID owner.
    session.execute(
        update(EmailMessage)
        .where(EmailMessage.id.in_(loser_ids))
        .values(dedupe_key=None),
        execution_options={"synchronize_session": False},
    )
    session.flush()

    existing_location_keys = set(
        session.execute(
            select(
                EmailLocation.outlook_store_id,
                EmailLocation.outlook_entry_id,
            ).where(
                EmailLocation.message_id == winner.id
            )
        ).all()
    )
    loser_locations = list(
        session.scalars(
            select(EmailLocation).where(
                EmailLocation.message_id.in_(loser_ids)
            )
        )
    )
    for location in loser_locations:
        key = (
            location.outlook_store_id,
            location.outlook_entry_id,
        )
        if key in existing_location_keys:
            session.delete(location)
        else:
            location.message_id = winner.id
            existing_location_keys.add(key)

    winner_has_recipients = bool(
        session.scalar(
            select(func.count())
            .select_from(EmailRecipient)
            .where(EmailRecipient.message_id == winner.id)
        )
    )
    if winner_has_recipients:
        session.execute(
            delete(EmailRecipient).where(
                EmailRecipient.message_id.in_(loser_ids)
            ),
            execution_options={"synchronize_session": False},
        )
    else:
        session.execute(
            update(EmailRecipient)
            .where(EmailRecipient.message_id.in_(loser_ids))
            .values(message_id=winner.id),
            execution_options={"synchronize_session": False},
        )

    winner_has_attachments = bool(
        session.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.message_id == winner.id)
        )
    )
    if winner_has_attachments:
        session.execute(
            delete(Attachment).where(
                Attachment.message_id.in_(loser_ids)
            ),
            execution_options={"synchronize_session": False},
        )
    else:
        session.execute(
            update(Attachment)
            .where(Attachment.message_id.in_(loser_ids))
            .values(message_id=winner.id),
            execution_options={"synchronize_session": False},
        )

    # Remove all vectors for the winner/losers and rebuild the surviving
    # source. This avoids stale Qdrant payloads with deleted source IDs.
    session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.source_type == "email",
            DocumentChunk.source_id.in_(all_ids),
        ),
        execution_options={"synchronize_session": False},
    )
    session.execute(
        delete(ProcessingJob).where(
            ProcessingJob.source_type == "email",
            ProcessingJob.source_id.in_(loser_ids),
        ),
        execution_options={"synchronize_session": False},
    )
    session.execute(
        delete(EmailMessage).where(
            EmailMessage.id.in_(loser_ids)
        ),
        execution_options={"synchronize_session": False},
    )
    session.flush()

    winner.dedupe_key = dedupe_key
    winner.processing_status = "updated"
    session.flush()
    return [
        point_id
        for point_id in stale_point_ids
        if point_id is not None
    ]


def resolve_message_identity(
    session: Session,
    *,
    mailbox_id: uuid.UUID,
    store_id: str,
    entry_id: str,
    dedupe_key: str,
    internet_message_id: str | None,
) -> tuple[EmailMessage | None, list[uuid.UUID], int]:
    """Resolve exact entry, physical location, and logical identity."""
    exact = _message_by_entry(
        session,
        mailbox_id=mailbox_id,
        entry_id=entry_id,
    )
    located = _message_by_location(
        session,
        store_id=store_id,
        entry_id=entry_id,
    )
    deduped = _message_by_dedupe(
        session,
        mailbox_id=mailbox_id,
        dedupe_key=dedupe_key,
        internet_message_id=internet_message_id,
    )

    candidates: list[EmailMessage] = []
    seen_ids: set[uuid.UUID] = set()
    for candidate in (exact, located, deduped):
        if (
            candidate is not None
            and candidate.id not in seen_ids
        ):
            candidates.append(candidate)
            seen_ids.add(candidate.id)

    if not candidates:
        return None, [], 0

    # Preserve the exact legacy EntryID owner so existing
    # workmind://email/<uuid> references remain stable.
    winner = exact or located or deduped
    assert winner is not None
    losers = [
        candidate
        for candidate in candidates
        if candidate.id != winner.id
    ]

    stale_points = _merge_message_rows(
        session,
        winner=winner,
        losers=losers,
        dedupe_key=dedupe_key,
    )
    return winner, stale_points, len(losers)


def upsert_message(
    session: Session,
    *,
    mailbox: Mailbox,
    thread: EmailThread | None,
    existing: EmailMessage | None,
    data: dict,
    dedupe_key: str,
    store_id: str,
    store_display_name: str,
    entry_id: str,
    folder_entry_id: str | None,
    folder_path: str,
    source_kind: str,
) -> tuple[EmailMessage, bool, bool, bool]:
    created = existing is None
    location_created = False

    if existing is None:
        existing = EmailMessage(
            mailbox_id=mailbox.id,
            outlook_entry_id=entry_id,
            outlook_store_id=store_id,
            folder_path=folder_path,
            subject="",
            body_text="",
            content_hash=data["content_hash"],
            dedupe_key=dedupe_key,
        )
        session.add(existing)
        session.flush()

    previous_hash = existing.content_hash
    has_primary_location = any(
        location.source_kind == "primary"
        for location in existing.locations
    )
    may_replace_canonical = (
        created
        or source_kind == "primary"
        or not has_primary_location
    )

    canonical_fields = {
        "outlook_entry_id",
        "outlook_store_id",
        "folder_path",
        "raw_json_path",
    }
    content_fields = {
        "internet_message_id",
        "conversation_id",
        "subject",
        "sender_name",
        "sender_email",
        "sent_at",
        "received_at",
        "body_text",
        "body_html",
        "content_hash",
        "has_attachments",
    }

    for key, value in data.items():
        if key in canonical_fields:
            if may_replace_canonical:
                setattr(existing, key, value)
        elif key in content_fields:
            if may_replace_canonical:
                setattr(existing, key, value)
        else:
            setattr(existing, key, value)

    existing.dedupe_key = existing.dedupe_key or dedupe_key
    if may_replace_canonical:
        existing.thread_id = thread.id if thread else None

    location = session.scalar(
        select(EmailLocation).where(
            EmailLocation.outlook_store_id == store_id,
            EmailLocation.outlook_entry_id == entry_id,
        )
    )
    if location is None:
        location = EmailLocation(
            message_id=existing.id,
            outlook_store_id=store_id,
            store_display_name=store_display_name,
            outlook_entry_id=entry_id,
            folder_entry_id=folder_entry_id,
            folder_path=folder_path,
            source_kind=source_kind,
        )
        session.add(location)
        location_created = True
    else:
        location.message_id = existing.id
        location.store_display_name = store_display_name
        location.folder_entry_id = folder_entry_id
        location.folder_path = folder_path
        location.source_kind = source_kind
        location.last_seen_at = datetime.now(UTC)

    if created and thread:
        thread.message_count += 1

    content_changed = (
        created
        or (
            may_replace_canonical
            and previous_hash != existing.content_hash
        )
    )
    session.flush()
    return (
        existing,
        created,
        content_changed,
        location_created,
    )


def replace_recipients(
    session: Session,
    message: EmailMessage,
    recipients: list[dict],
) -> None:
    message.recipients.clear()
    for recipient in recipients:
        message.recipients.append(
            EmailRecipient(**recipient)
        )
    session.flush()


def enqueue_message_processing(
    session: Session,
    message_id: uuid.UUID,
) -> None:
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.job_type == "index_email",
            ProcessingJob.source_type == "email",
            ProcessingJob.source_id == message_id,
            ProcessingJob.status.in_(
                ("pending", "processing")
            ),
        )
    )
    if existing is not None:
        return
    session.add(
        ProcessingJob(
            job_type="index_email",
            source_type="email",
            source_id=message_id,
            payload_json={"message_id": str(message_id)},
        )
    )



def enqueue_attachment_processing(
    session: Session,
    attachment_id: uuid.UUID,
) -> None:
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.job_type == "index_attachment",
            ProcessingJob.source_type == "attachment",
            ProcessingJob.source_id == attachment_id,
            ProcessingJob.status.in_(("pending", "processing")),
        )
    )
    if existing is not None:
        return

    session.add(
        ProcessingJob(
            job_type="index_attachment",
            source_type="attachment",
            source_id=attachment_id,
            payload_json={
                "attachment_id": str(attachment_id),
            },
        )
    )
