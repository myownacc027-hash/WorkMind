from packages.processing.chunking import build_email_document, chunk_text, normalize_email_text


def test_normalize_email_text_collapses_blank_lines() -> None:
    assert normalize_email_text("a\r\n\r\n\r\nb") == "a\n\nb"


def test_chunk_text_preserves_content() -> None:
    text = "Первый абзац.\n\n" + ("Второй длинный абзац. " * 40)
    chunks = chunk_text(text, max_chars=300, overlap_chars=40)
    assert len(chunks) >= 2
    assert chunks[0].index == 0
    assert all(chunk.text for chunk in chunks)


def test_build_email_document() -> None:
    value = build_email_document(
        subject="Тема",
        sender_name="Иван",
        sender_email="ivan@example.com",
        occurred_at="2026-07-15T10:00:00+00:00",
        body_text="Текст",
    )
    assert "Subject: Тема" in value
    assert "ivan@example.com" in value
    assert value.endswith("Текст")
