from pathlib import Path


SOURCE = Path(
    "packages/knowledge/extraction.py"
).read_text(encoding="utf-8")


def test_thread_context_is_bounded_to_target_message_time() -> None:
    assert "target_date = message.received_at or message.sent_at or message.created_at" in SOURCE
    assert ") <= target_date" in SOURCE


def test_packet_has_bounded_context_limits() -> None:
    assert "knowledge_extraction_max_chunks" in SOURCE
    assert "knowledge_extraction_max_context_chars" in SOURCE
