from datetime import UTC, datetime

from packages.database.repositories import calculate_message_hash, normalize_subject


def test_normalize_subject_removes_reply_prefixes() -> None:
    assert normalize_subject("RE: FW: Ответ: Задача 123") == "задача 123"


def test_message_hash_is_stable() -> None:
    occurred_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    first = calculate_message_hash("Subject", "a@example.com", "Body", occurred_at)
    second = calculate_message_hash("Subject", "a@example.com", "Body", occurred_at)
    assert first == second
