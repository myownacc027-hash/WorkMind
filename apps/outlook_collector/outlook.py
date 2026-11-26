from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.common.config import Settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    Mailbox,
    ProcessingJob,
    SyncState,
)
from packages.database.repositories import (
    calculate_dedupe_key,
    calculate_message_hash,
    enqueue_attachment_processing,
    enqueue_message_processing,
    resolve_message_identity,
    get_or_create_mailbox,
    get_or_create_thread,
    replace_recipients,
    upsert_message,
)
from packages.search.indexer import delete_points
from packages.search.qdrant import get_qdrant_client
from packages.storage import store_attachment_file

logger = structlog.get_logger(__name__)

MAIL_ITEM_CLASS = 43
OL_FOLDER_INBOX = 6
OL_FOLDER_SENT_MAIL = 5
PR_INTERNET_MESSAGE_ID = (
    "http://schemas.microsoft.com/mapi/proptag/0x1035001E"
)
PR_SMTP_ADDRESS = (
    "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"
)
RECIPIENT_TYPES = {1: "to", 2: "cc", 3: "bcc"}
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(slots=True)
class FolderSpec:
    name: str
    folder: Any
    date_attr: str


class OutlookCollectorError(RuntimeError):
    pass


def _safe_text(value: object) -> str:
    """Normalize Outlook UTF-16 strings before hashing, storage and indexing.

    COM can expose UTF-16 surrogate code units in a Python string. Decode
    valid pairs losslessly; replace only unpaired units and PostgreSQL-
    incompatible NULs. Valid Unicode is returned unchanged.
    """
    if value is None:
        return ""
    text = str(value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-16-le", errors="surrogatepass").decode(
            "utf-16-le", errors="replace"
        )
        logger.warning("outlook_text_normalized", reason="utf16_surrogates")
    if "\x00" in text:
        text = text.replace("\x00", "\ufffd")
        logger.warning("outlook_text_normalized", reason="embedded_nul")
    return text


def _identity_text(value: object) -> str:
    """Never silently alter opaque MAPI identities used for lookup/cursors."""
    text = "" if value is None else str(value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OutlookCollectorError("Invalid Unicode in Outlook identity.") from exc
    if "\x00" in text:
        raise OutlookCollectorError("Embedded NUL in Outlook identity.")
    return text


def _safe_filename(filename: str) -> str:
    cleaned = INVALID_FILENAME.sub("_", filename).strip(" .")
    return cleaned[:180] or "attachment.bin"


def _to_utc(value: object) -> datetime | None:
    if not value or not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.astimezone().astimezone(UTC)
    return value.astimezone(UTC)


def _property(item: Any, schema: str) -> str | None:
    try:
        value = item.PropertyAccessor.GetProperty(schema)
        return _safe_text(value).strip() or None
    except Exception:
        return None


def _sender_smtp(item: Any) -> str | None:
    try:
        sender_type = _safe_text(
            getattr(item, "SenderEmailType", "")
        )
        if sender_type.upper() == "EX":
            sender = item.Sender
            exchange_user = (
                sender.GetExchangeUser() if sender else None
            )
            if (
                exchange_user
                and exchange_user.PrimarySmtpAddress
            ):
                return _safe_text(
                    exchange_user.PrimarySmtpAddress
                ).lower()
            value = _property(item, PR_SMTP_ADDRESS)
            if value:
                return value.lower()
        value = _safe_text(
            getattr(item, "SenderEmailAddress", "")
        ).strip()
        return value.lower() or None
    except Exception:
        return None


def _recipient_smtp(recipient: Any) -> str | None:
    try:
        entry = recipient.AddressEntry
        if entry and _safe_text(entry.Type).upper() == "EX":
            exchange_user = entry.GetExchangeUser()
            if (
                exchange_user
                and exchange_user.PrimarySmtpAddress
            ):
                return _safe_text(
                    exchange_user.PrimarySmtpAddress
                ).lower()
            value = entry.PropertyAccessor.GetProperty(
                PR_SMTP_ADDRESS
            )
            return _safe_text(value).lower() or None
        address = _safe_text(
            getattr(entry, "Address", "")
        ).strip()
        return address.lower() or None
    except Exception:
        return None


def _extract_recipients(item: Any) -> list[dict]:
    result: list[dict] = []
    try:
        recipients = item.Recipients
        for index in range(1, recipients.Count + 1):
            recipient = recipients.Item(index)
            result.append(
                {
                    "recipient_type": RECIPIENT_TYPES.get(
                        int(recipient.Type),
                        "to",
                    ),
                    "display_name": _safe_text(
                        recipient.Name
                    ).strip()
                    or None,
                    "email": _recipient_smtp(recipient),
                }
            )
    except Exception as exc:
        logger.warning(
            "recipient_extraction_failed",
            error=str(exc),
        )
    return result


def _folder_path(folder: Any) -> str:
    try:
        return _safe_text(folder.FolderPath)
    except Exception:
        return _safe_text(folder.Name)


def _folder_entry_id(folder: Any) -> str | None:
    try:
        return _identity_text(folder.EntryID).strip() or None
    except Exception:
        return None


def _find_child_folder(root: Any, path: str) -> Any:
    current = root
    parts = [
        part.strip()
        for part in re.split(r"[\\/]", path)
        if part.strip()
    ]
    for part in parts:
        found = None
        for index in range(1, current.Folders.Count + 1):
            candidate = current.Folders.Item(index)
            if (
                _safe_text(candidate.Name).casefold()
                == part.casefold()
            ):
                found = candidate
                break
        if found is None:
            raise OutlookCollectorError(
                f"Outlook folder not found: {path}"
            )
        current = found
    return current


def _iter_recent_items(
    folder: Any,
    *,
    date_attr: str,
    since: datetime,
    max_items: int,
    on_error: Callable[[Exception], None] | None = None,
) -> Iterator[Any]:
    items = folder.Items
    items.Sort(f"[{date_attr}]", True)
    emitted = 0
    for index in range(1, items.Count + 1):
        try:
            item = items.Item(index)
            if int(getattr(item, "Class", 0)) != MAIL_ITEM_CLASS:
                continue
            occurred_at = _to_utc(
                getattr(item, date_attr, None)
            )
            if occurred_at and occurred_at < since:
                break
            yield item
            emitted += 1
            if max_items and emitted >= max_items:
                break
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            logger.warning(
                "outlook_item_skipped",
                folder=_folder_path(folder),
                index=index,
                error=str(exc),
            )


def _attachment_name(
    attachment: Any,
    index: int,
) -> str:
    for attribute in ("FileName", "DisplayName"):
        try:
            value = _safe_text(
                getattr(attachment, attribute, "")
            ).strip()
        except Exception:
            continue
        if value:
            return _safe_filename(value)
    return f"unavailable_attachment_{index}"


def _save_attachment(
    attachment: Any,
    directory: Path,
    *,
    index: int,
    session: Session,
    settings: Settings,
) -> tuple[str, Any]:
    filename = _attachment_name(attachment, index)
    target = directory / filename
    if target.exists():
        target = directory / (
            f"{target.stem}_{index}{target.suffix}"
        )
    attachment.SaveAsFile(str(target))

    try:
        stored_object = store_attachment_file(
            session,
            settings=settings,
            source_path=target,
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return filename, stored_object


def _raw_payload(
    item: Any,
    *,
    folder_path: str,
    recipients: list[dict],
) -> dict:
    return {
        "outlook_entry_id": _identity_text(
            getattr(item, "EntryID", "")
        ),
        "internet_message_id": _property(
            item,
            PR_INTERNET_MESSAGE_ID,
        ),
        "conversation_id": _safe_text(
            getattr(item, "ConversationID", "")
        ).strip()
        or None,
        "folder_path": _safe_text(folder_path),
        "subject": _safe_text(
            getattr(item, "Subject", "")
        ),
        "sender_name": _safe_text(
            getattr(item, "SenderName", "")
        ).strip()
        or None,
        "sender_email": _sender_smtp(item),
        "sent_at": _to_utc(
            getattr(item, "SentOn", None)
        ),
        "received_at": _to_utc(
            getattr(item, "ReceivedTime", None)
        ),
        "body_text": _safe_text(
            getattr(item, "Body", "")
        ),
        "body_html": _safe_text(
            getattr(item, "HTMLBody", "")
        )
        or None,
        "recipients": recipients,
    }


def connect_outlook() -> tuple[Any, Any]:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OutlookCollectorError(
            "pywin32 is not installed. "
            "Run: pip install -e .[windows]"
        ) from exc

    pythoncom.CoInitialize()
    try:
        application = win32com.client.Dispatch(
            "Outlook.Application"
        )
        namespace = application.GetNamespace("MAPI")
        return application, namespace
    except Exception as exc:
        raise OutlookCollectorError(
            "Could not connect to classic Outlook. "
            "Start Outlook 2019 and try again."
        ) from exc


def _select_account_store(
    namespace: Any,
    smtp_address: str,
) -> tuple[Any, Any]:
    target = smtp_address.strip().casefold()
    if not target:
        raise OutlookCollectorError(
            "OUTLOOK_ACCOUNT_SMTP is not configured."
        )

    available: list[str] = []
    accounts = namespace.Accounts
    for index in range(1, accounts.Count + 1):
        account = accounts.Item(index)
        smtp = _safe_text(
            getattr(account, "SmtpAddress", "")
        ).strip()
        username = _safe_text(
            getattr(account, "UserName", "")
        ).strip()
        label = smtp or username or f"account-{index}"
        available.append(label)
        if target not in {
            smtp.casefold(),
            username.casefold(),
        }:
            continue

        store = getattr(account, "DeliveryStore", None)
        if store is None:
            raise OutlookCollectorError(
                f"Outlook account {label!r} "
                "does not have a delivery store."
            )
        return account, store

    raise OutlookCollectorError(
        "Outlook account not found: "
        f"{smtp_address}. Available accounts: "
        f"{', '.join(available) or 'none'}"
    )


def resolve_folders(
    store: Any,
    configured: list[str],
) -> list[FolderSpec]:
    result: list[FolderSpec] = []
    default_inbox = store.GetDefaultFolder(
        OL_FOLDER_INBOX
    )
    default_sent = store.GetDefaultFolder(
        OL_FOLDER_SENT_MAIL
    )
    store_root = store.GetRootFolder()

    for name in configured:
        normalized = name.strip().casefold()
        if normalized in {"inbox", "входящие"}:
            result.append(
                FolderSpec(
                    name=name,
                    folder=default_inbox,
                    date_attr="ReceivedTime",
                )
            )
        elif normalized in {
            "sent items",
            "sent",
            "отправленные",
        }:
            result.append(
                FolderSpec(
                    name=name,
                    folder=default_sent,
                    date_attr="SentOn",
                )
            )
        else:
            folder = _find_child_folder(
                store_root,
                name,
            )
            result.append(
                FolderSpec(
                    name=name,
                    folder=folder,
                    date_attr="ReceivedTime",
                )
            )
    return result



def _needs_attachment_refresh(
    message: Any,
    *,
    save_enabled: bool,
    created: bool,
    content_changed: bool,
) -> bool:
    if not save_enabled:
        return False
    if created or content_changed:
        return True
    if message.has_attachments and not message.attachments:
        return True
    return any(
        attachment.archive_path is None
        and attachment.processing_status == "error"
        for attachment in message.attachments
    )


def ingest_mail_item(
    session: Session,
    settings: Settings,
    *,
    mailbox: Mailbox,
    item: Any,
    store_id: str,
    store_display_name: str,
    folder_path: str,
    folder_entry_id: str | None,
    source_kind: str,
) -> dict[str, int | str]:
    recipients = _extract_recipients(item)
    payload = _raw_payload(
        item,
        folder_path=folder_path,
        recipients=recipients,
    )
    entry_id = payload["outlook_entry_id"]
    if not entry_id:
        raise OutlookCollectorError(
            "Outlook item has no EntryID."
        )

    occurred_at = (
        payload["received_at"] or payload["sent_at"]
    )
    content_hash = calculate_message_hash(
        payload["subject"],
        payload["sender_email"],
        payload["body_text"],
        occurred_at,
    )
    dedupe_key = calculate_dedupe_key(
        internet_message_id=payload[
            "internet_message_id"
        ],
        subject=payload["subject"],
        sender=payload["sender_email"],
        body=payload["body_text"],
        occurred_at=occurred_at,
        recipients=recipients,
    )

    (
        existing,
        stale_qdrant_point_ids,
        merged_rows,
    ) = resolve_message_identity(
        session,
        mailbox_id=mailbox.id,
        store_id=store_id,
        entry_id=entry_id,
        dedupe_key=dedupe_key,
        internet_message_id=payload[
            "internet_message_id"
        ],
    )
    if (
        existing is not None
        and existing.thread is not None
        and source_kind == "archive"
    ):
        thread = existing.thread
    else:
        thread = get_or_create_thread(
            session,
            mailbox_id=mailbox.id,
            conversation_id=payload[
                "conversation_id"
            ],
            subject=payload["subject"],
            occurred_at=occurred_at,
        )

    message, created, content_changed, location_created = (
        upsert_message(
            session,
            mailbox=mailbox,
            thread=thread,
            existing=existing,
            data={
                "outlook_entry_id": entry_id,
                "outlook_store_id": store_id,
                "internet_message_id": payload[
                    "internet_message_id"
                ],
                "conversation_id": payload[
                    "conversation_id"
                ],
                "folder_path": folder_path,
                "subject": payload["subject"],
                "sender_name": payload[
                    "sender_name"
                ],
                "sender_email": payload[
                    "sender_email"
                ],
                "sent_at": payload["sent_at"],
                "received_at": payload[
                    "received_at"
                ],
                "body_text": payload["body_text"],
                "body_html": payload["body_html"],
                "content_hash": content_hash,
                "raw_json_path": None,
                "has_attachments": int(
                    getattr(item.Attachments, "Count", 0)
                )
                > 0,
            },
            dedupe_key=dedupe_key,
            store_id=store_id,
            store_display_name=store_display_name,
            entry_id=entry_id,
            folder_entry_id=folder_entry_id,
            folder_path=folder_path,
            source_kind=source_kind,
        )
    )

    date_for_path = occurred_at or datetime.now(UTC)
    message_dir = (
        settings.mail_archive_dir
        / f"{date_for_path.year:04d}"
        / f"{date_for_path.month:02d}"
        / str(message.id)
    )
    message_dir.mkdir(parents=True, exist_ok=True)
    location_key = hashlib.sha256(
        f"{store_id}:{entry_id}".encode(
            "utf-8",
            errors="replace",
        )
    ).hexdigest()[:20]
    raw_path = message_dir / f"{location_key}.json"

    if (
        settings.outlook_save_raw_json
        and (
            created
            or content_changed
            or not raw_path.exists()
        )
    ):
        json_payload = {
            **payload,
            "store_display_name": store_display_name,
            "store_id": store_id,
            "source_kind": source_kind,
            "folder_entry_id": folder_entry_id,
            "sent_at": (
                payload["sent_at"].isoformat()
                if payload["sent_at"]
                else None
            ),
            "received_at": (
                payload["received_at"].isoformat()
                if payload["received_at"]
                else None
            ),
        }
        raw_path.write_text(
            json.dumps(
                json_payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if (
            source_kind == "primary"
            or not message.raw_json_path
        ):
            message.raw_json_path = str(raw_path)

    if created or content_changed:
        message.processing_status = (
            "new" if created else "updated"
        )
        replace_recipients(
            session,
            message,
            recipients,
        )

    should_refresh_attachments = (
        _needs_attachment_refresh(
            message,
            save_enabled=settings.outlook_save_attachments,
            created=created,
            content_changed=content_changed,
        )
    )
    attachment_count = 0
    attachment_failed = 0

    if should_refresh_attachments:
        old_attachment_ids = [
            attachment.id
            for attachment in message.attachments
        ]
        if old_attachment_ids:
            stale_qdrant_point_ids.extend(
                point_id
                for point_id in session.scalars(
                    select(DocumentChunk.qdrant_point_id).where(
                        DocumentChunk.source_type == "attachment",
                        DocumentChunk.source_id.in_(old_attachment_ids),
                        DocumentChunk.qdrant_point_id.is_not(None),
                    )
                )
                if point_id is not None
            )
            session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.source_type == "attachment",
                    DocumentChunk.source_id.in_(old_attachment_ids),
                )
            )
            session.execute(
                delete(ProcessingJob).where(
                    ProcessingJob.source_type == "attachment",
                    ProcessingJob.source_id.in_(old_attachment_ids),
                )
            )

        message.attachments.clear()
        saved_attachment_rows: list[Attachment] = []
        attachment_dir = (
            settings.attachment_archive_dir
            / str(message.id)
        )
        if attachment_dir.exists():
            shutil.rmtree(attachment_dir)
        attachment_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        attachments = item.Attachments
        for index in range(1, attachments.Count + 1):
            attachment = attachments.Item(index)
            try:
                filename, stored_object = (
                    _save_attachment(
                        attachment,
                        attachment_dir,
                        index=index,
                        session=session,
                        settings=settings,
                    )
                )
            except Exception as exc:
                failed_name = _attachment_name(
                    attachment,
                    index,
                )
                message.attachments.append(
                    Attachment(
                        filename=failed_name,
                        archive_path=None,
                        content_hash=None,
                        size_bytes=None,
                        mime_type=None,
                        processing_status="error",
                        last_error=_safe_text(exc),
                        metadata_json={
                            "outlook_index": index,
                            "save_error": True,
                        },
                    )
                )
                attachment_failed += 1
                logger.warning(
                    "attachment_sync_failed",
                    folder=folder_path,
                    message_id=str(message.id),
                    subject=payload["subject"],
                    attachment_index=index,
                    attachment_name=failed_name,
                    error=str(exc),
                )
                continue

            mime_type, _ = mimetypes.guess_type(
                filename
            )
            attachment_row = Attachment(
                filename=filename,
                archive_path=stored_object.storage_path,
                stored_object_id=stored_object.id,
                content_hash=stored_object.sha256,
                size_bytes=stored_object.size_bytes,
                mime_type=mime_type,
                metadata_json={
                    "outlook_index": index,
                    "content_addressed_storage": True,
                },
            )
            message.attachments.append(attachment_row)
            saved_attachment_rows.append(attachment_row)
            attachment_count += 1

        session.flush()
        for attachment_row in saved_attachment_rows:
            enqueue_attachment_processing(
                session,
                attachment_row.id,
            )
            attachment_row.processing_status = "queued"

    if merged_rows:
        content_changed = True
        message.processing_status = "updated"

    if content_changed:
        enqueue_message_processing(
            session,
            message.id,
        )

    outcome = "unchanged"
    if created:
        outcome = "created"
    elif merged_rows or location_created:
        outcome = "deduplicated"
    elif content_changed:
        outcome = "updated"

    session.commit()

    if stale_qdrant_point_ids:
        try:
            delete_points(
                get_qdrant_client(),
                stale_qdrant_point_ids,
            )
        except Exception as exc:
            logger.warning(
                "stale_qdrant_cleanup_failed",
                message_id=str(message.id),
                points=len(stale_qdrant_point_ids),
                error=str(exc),
            )

    return {
        "outcome": outcome,
        "attachments": attachment_count,
        "attachment_failed": attachment_failed,
        "message_id": str(message.id),
        "occurred_at": (
            occurred_at.isoformat() if occurred_at else ""
        ),
    }


def collect_mail(
    session: Session,
    settings: Settings,
    *,
    incremental: bool = False,
    overlap_minutes: int = 30,
) -> dict[str, int]:
    _, namespace = connect_outlook()
    account, store = _select_account_store(
        namespace,
        settings.outlook_account_smtp,
    )
    folders = resolve_folders(
        store,
        settings.outlook_folders,
    )
    now = datetime.now(UTC)
    full_since = now - timedelta(
        days=settings.outlook_lookback_days
    )
    store_display_name = _safe_text(
        getattr(store, "DisplayName", "")
    ).strip()
    store_id = (
        _identity_text(
            getattr(store, "StoreID", "")
        ).strip()
        or f"display:{store_display_name}"
    )
    account_smtp = _safe_text(
        getattr(account, "SmtpAddress", "")
    ).strip()
    mailbox = get_or_create_mailbox(
        session,
        name=(
            settings.outlook_mailbox_name
            or account_smtp
            or settings.outlook_account_smtp
        ),
        store_id=store_id,
    )
    session.commit()

    counters = {
        "seen": 0,
        "created": 0,
        "updated": 0,
        "deduplicated": 0,
        "unchanged": 0,
        "failed": 0,
        "item_skipped": 0,
        "attachments": 0,
        "attachment_failed": 0,
    }

    for spec in folders:
        path = _folder_path(spec.folder)
        folder_id = _folder_entry_id(spec.folder)
        latest_timestamp: datetime | None = None

        sync_state = (
            session.query(SyncState)
            .filter_by(
                mailbox_id=mailbox.id,
                folder_path=path,
            )
            .one_or_none()
        )
        if sync_state is None:
            sync_state = SyncState(
                mailbox_id=mailbox.id,
                folder_path=path,
            )
            session.add(sync_state)
            session.flush()

        folder_since = full_since
        mode = "full"
        if (
            incremental
            and sync_state.last_item_timestamp
        ):
            folder_since = (
                sync_state.last_item_timestamp
                - timedelta(minutes=overlap_minutes)
            )
            if folder_since > now:
                folder_since = now
            mode = "incremental"

        logger.info(
            "folder_sync_started",
            folder=path,
            since=folder_since.isoformat(),
            mode=mode,
        )

        folder_failed_before = counters["failed"]

        def _record_item_read_error(
            _exc: Exception,
        ) -> None:
            counters["failed"] += 1
            counters["item_skipped"] += 1

        for item in _iter_recent_items(
            spec.folder,
            date_attr=spec.date_attr,
            since=folder_since,
            max_items=(
                settings.outlook_max_messages_per_folder
            ),
            on_error=_record_item_read_error,
        ):
            counters["seen"] += 1
            try:
                result = ingest_mail_item(
                    session,
                    settings,
                    mailbox=mailbox,
                    item=item,
                    store_id=store_id,
                    store_display_name=store_display_name,
                    folder_path=path,
                    folder_entry_id=folder_id,
                    source_kind="primary",
                )
                outcome = str(result["outcome"])
                counters[outcome] += 1
                counters["attachments"] += int(
                    result["attachments"]
                )
                counters["attachment_failed"] += int(
                    result["attachment_failed"]
                )
                occurred_text = str(
                    result["occurred_at"]
                )
                if occurred_text:
                    occurred_at = datetime.fromisoformat(
                        occurred_text
                    )
                    if (
                        latest_timestamp is None
                        or occurred_at > latest_timestamp
                    ):
                        latest_timestamp = occurred_at
            except Exception as exc:
                session.rollback()
                counters["failed"] += 1
                logger.exception(
                    "message_sync_failed",
                    folder=path,
                    error=str(exc),
                )

        sync_state.last_synced_at = datetime.now(UTC)
        if latest_timestamp is not None:
            if (
                sync_state.last_item_timestamp is None
                or latest_timestamp
                > sync_state.last_item_timestamp
            ):
                sync_state.last_item_timestamp = (
                    latest_timestamp
                )
        folder_failures = (
            counters["failed"] - folder_failed_before
        )
        sync_state.last_error = (
            f"{folder_failures} mail item(s) failed or "
            "were skipped during the latest scan."
            if folder_failures
            else None
        )
        sync_state.metadata_json = {
            **(sync_state.metadata_json or {}),
            "last_mode": mode,
            "last_since": folder_since.isoformat(),
            "overlap_minutes": (
                overlap_minutes
                if mode == "incremental"
                else 0
            ),
        }
        session.commit()
        logger.info(
            "folder_sync_completed",
            folder=path,
            mode=mode,
        )

    mailbox.updated_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "outlook_sync_completed",
        mailbox=mailbox.name,
        counters=counters,
    )
    return counters
