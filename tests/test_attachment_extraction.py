from pathlib import Path

from docx import Document
from openpyxl import Workbook

from packages.processing.attachments import extract_attachment


def test_extract_docx(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("Тестовый документ")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Код"
    table.cell(0, 1).text = "123"
    document.save(path)

    result = extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".docx"},
    )
    assert result.status == "extracted"
    assert "Тестовый документ" in result.text
    assert "123" in result.text


def test_extract_xlsx(tmp_path: Path) -> None:
    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Данные"
    sheet.append(["КИЗ", "Статус"])
    sheet.append(["0101", "Кондиция"])
    workbook.save(path)

    result = extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".xlsx"},
    )
    assert result.status == "extracted"
    assert "Данные" in result.text
    assert "Кондиция" in result.text


def test_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "sample.xls"
    path.write_bytes(b"legacy")
    result = extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".xlsx"},
    )
    assert result.status == "unsupported"
