from datetime import UTC, datetime

from packages.database.repositories import (
    calculate_dedupe_key,
)


def test_message_id_has_priority() -> None:
    first = calculate_dedupe_key(
        internet_message_id="<ABC@example.com>",
        subject="One",
        sender="a@example.com",
        body="Body one",
        occurred_at=datetime(
            2026, 1, 1, tzinfo=UTC
        ),
        recipients=[],
    )
    second = calculate_dedupe_key(
        internet_message_id="abc@example.com",
        subject="Different",
        sender="b@example.com",
        body="Different body",
        occurred_at=datetime(
            2025, 1, 1, tzinfo=UTC
        ),
        recipients=[],
    )
    assert first == second
    assert first.startswith("mid:")


def test_fingerprint_is_stable_for_recipient_order() -> None:
    kwargs = {
        "internet_message_id": None,
        "subject": "Re: Test",
        "sender": "sender@example.com",
        "body": "Hello   world",
        "occurred_at": datetime(
            2026, 1, 1, 12, 0, tzinfo=UTC
        ),
    }
    first = calculate_dedupe_key(
        **kwargs,
        recipients=[
            {
                "recipient_type": "to",
                "email": "a@example.com",
            },
            {
                "recipient_type": "cc",
                "email": "b@example.com",
            },
        ],
    )
    second = calculate_dedupe_key(
        **kwargs,
        recipients=[
            {
                "recipient_type": "cc",
                "email": "b@example.com",
            },
            {
                "recipient_type": "to",
                "email": "a@example.com",
            },
        ],
    )
    assert first == second
    assert first.startswith("fp:")
