from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int
    text: str


_REPLY_MARKERS = (
    "-----original message-----",
    "-----исходное сообщение-----",
    "от:",
    "from:",
)


def normalize_email_text(text: str) -> str:
    """Normalize Outlook body text without destructively removing useful content."""
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.splitlines()]

    compact: list[str] = []
    blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not blank:
                compact.append("")
            blank = True
            continue
        blank = False
        compact.append(line)

    return "\n".join(compact).strip()


def build_email_document(
    *,
    subject: str,
    sender_name: str | None,
    sender_email: str | None,
    occurred_at: str | None,
    body_text: str,
) -> str:
    sender = sender_name or sender_email or "unknown"
    if sender_email and sender_email.casefold() not in sender.casefold():
        sender = f"{sender} <{sender_email}>"

    parts = [
        f"Subject: {subject.strip()}",
        f"From: {sender}",
    ]
    if occurred_at:
        parts.append(f"Date: {occurred_at}")
    parts.extend(["", normalize_email_text(body_text)])
    return "\n".join(parts).strip()


def chunk_text(text: str, *, max_chars: int, overlap_chars: int) -> list[TextChunk]:
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be >= 0 and < max_chars")

    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            step = max_chars - overlap_chars
            while start < len(paragraph):
                part = paragraph[start : start + max_chars].strip()
                if part:
                    chunks.append(part)
                start += step
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        chunks.append(current)
        if overlap_chars:
            tail = current[-overlap_chars:].strip()
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
            if len(current) > max_chars:
                current = paragraph
        else:
            current = paragraph

    if current:
        chunks.append(current)

    return [TextChunk(index=index, text=value) for index, value in enumerate(chunks)]



def build_attachment_document(
    *,
    filename: str,
    subject: str,
    sender_name: str | None,
    sender_email: str | None,
    occurred_at: str | None,
    extracted_text: str,
) -> str:
    sender = sender_name or sender_email or "unknown"
    if sender_email and sender_email.casefold() not in sender.casefold():
        sender = f"{sender} <{sender_email}>"

    parts = [
        f"Attachment: {filename.strip()}",
        f"Email subject: {subject.strip()}",
        f"Email sender: {sender}",
    ]
    if occurred_at:
        parts.append(f"Email date: {occurred_at}")
    parts.extend(["", normalize_email_text(extracted_text)])
    return "\n".join(parts).strip()



def build_external_document(
    *,
    filename: str,
    source_date: str | None,
    mime_type: str | None,
    extracted_text: str,
) -> str:
    parts = [f"Document: {filename.strip()}"]
    if source_date:
        parts.append(f"Source date: {source_date}")
    if mime_type:
        parts.append(f"MIME type: {mime_type}")
    parts.extend(["", normalize_email_text(extracted_text)])
    return "\n".join(parts).strip()
