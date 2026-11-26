import json
from types import SimpleNamespace

import pytest

from apps.outlook_collector import outlook
from apps.history_importer import main as history
from packages.database.repositories import calculate_dedupe_key, calculate_message_hash


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        (123, "123"),
        ("normal text", "normal text"),
        ("\u041f\u0438\u0441\u044c\u043c\u043e", "\u041f\u0438\u0441\u044c\u043c\u043e"),
        ("\U0001f600", "\U0001f600"),
        ("\ud83d\ude00", "\U0001f600"),
        ("a\ud800b\udc00c", "a\ufffdb\ufffdc"),
        ("a\x00b", "a\ufffdb"),
        ("\ud83d\ude00\x00\ud800", "\U0001f600\ufffd\ufffd"),
    ],
)
def test_safe_outlook_text(raw, expected):
    result = outlook._safe_text(raw)
    assert result == expected
    result.encode("utf-8", errors="strict")
    assert "\x00" not in result
    assert outlook._safe_text(result) == result


def test_all_mail_text_normalized_before_hashing_and_serialization():
    recipient = SimpleNamespace(
        Type=1,
        Name="Name\ud83d\ude00",
        AddressEntry=SimpleNamespace(Type="SMTP", Address="test@example.invalid"),
    )
    item = SimpleNamespace(
        EntryID="entry-id",
        ConversationID="conversation-id",
        Subject="Subject\ud83d\ude00",
        SenderName="Sender\ud800",
        SenderEmailType="SMTP",
        SenderEmailAddress="sender@example.invalid",
        Body="body\ud83d\ude00\x00",
        HTMLBody="<p>\udc00</p>",
        PropertyAccessor=SimpleNamespace(GetProperty=lambda schema: "<message@example.invalid>"),
        Recipients=SimpleNamespace(Count=1, Item=lambda index: recipient),
    )
    recipients = outlook._extract_recipients(item)
    payload = outlook._raw_payload(item, folder_path="folder", recipients=recipients)
    assert payload["subject"] == "Subject\U0001f600"
    assert payload["sender_name"] == "Sender\ufffd"
    assert payload["body_text"] == "body\U0001f600\ufffd"
    assert payload["body_html"] == "<p>\ufffd</p>"
    assert recipients[0]["display_name"] == "Name\U0001f600"
    assert payload["outlook_entry_id"] == "entry-id"
    # Same canonical text is suitable for PostgreSQL text, JSON and hashing.
    json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="strict")
    for field in ("subject", "sender_name", "body_text", "body_html"):
        payload[field].encode("utf-8", errors="strict")
        assert "\x00" not in payload[field]
    expected_hash = calculate_message_hash(
        "Subject\U0001f600", "sender@example.invalid", "body\U0001f600\ufffd", None
    )
    assert calculate_message_hash(
        payload["subject"], payload["sender_email"], payload["body_text"], None
    ) == expected_hash
    assert calculate_dedupe_key(
        internet_message_id=payload["internet_message_id"],
        subject=payload["subject"], sender=payload["sender_email"],
        body=payload["body_text"], occurred_at=None, recipients=recipients,
    ).startswith("mid:")


def test_attachment_name_normalized_before_saving():
    attachment = SimpleNamespace(FileName="report\ud83d\ude00\ud800.xlsx")
    assert outlook._attachment_name(attachment, 1) == "report\U0001f600\ufffd.xlsx"


@pytest.mark.parametrize("value", ["id\ud800", "id\x00", "id\ud83d\ude00"])
def test_invalid_mapi_identity_not_silently_rewritten(value):
    with pytest.raises(outlook.OutlookCollectorError):
        outlook._identity_text(value)
    spec = history.HistoricalFolder(
        store_id="store", store_display_name="archive", source_kind="archive",
        folder_id="folder", folder_path="archive/inbox", date_attr="ReceivedTime",
    )
    with pytest.raises(history.OutlookFolderUnavailable):
        history._require_history_entry_id(SimpleNamespace(EntryID=value), spec)
