from pathlib import Path


def test_extraction_fields_are_on_attachment() -> None:
    source = Path("packages/database/models.py").read_text(encoding="utf-8")
    attachment_section = source.split("class Attachment(Base):", 1)[1].split(
        "class DocumentChunk(Base):", 1
    )[0]
    email_section = source.split("class EmailMessage(Base):", 1)[1].split(
        "class EmailLocation(Base):", 1
    )[0]
    assert "extracted_text_path" in attachment_section
    assert "extracted_text_path" not in email_section
