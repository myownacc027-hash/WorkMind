from __future__ import annotations

import zipfile
from pathlib import Path

import packages.processing.attachments as attachment_module
from packages.processing.attachments import extract_attachment


def _write_case_variant_xlsx(path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
      <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
      <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
    </Types>
    """
    root_rels = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
    </Relationships>
    """
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>
        <sheet name="Данные" sheetId="1" r:id="rId1"/>
      </sheets>
    </workbook>
    """
    workbook_rels = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
    </Relationships>
    """
    worksheet = """<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1">
          <c r="A1" t="s"><v>0</v></c>
          <c r="B1" t="inlineStr"><is><t>Кондиция</t></is></c>
        </row>
      </sheetData>
    </worksheet>
    """
    shared_strings = """<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">
      <si><t>КИЗ</t></si>
    </sst>
    """

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            workbook_rels,
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            worksheet,
        )
        # Deliberately wrong case: openpyxl expects
        # xl/sharedStrings.xml.
        archive.writestr(
            "xl/SharedStrings.xml",
            shared_strings,
        )


def _write_minimal_docx(path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
    </Types>
    """
    root_rels = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
    </Relationships>
    """
    document = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Восстановленный текст DOCX</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)


def test_xlsx_fallback_recovers_case_variant_shared_strings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken.xlsx"
    _write_case_variant_xlsx(path)

    result = extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".xlsx"},
    )

    assert result.status == "extracted"
    assert result.method == "ooxml-xlsx-fallback"
    assert "КИЗ" in result.text
    assert "Кондиция" in result.text
    assert result.metadata["fallback_used"] is True


def test_docx_fallback_ignores_broken_media(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "broken.docx"
    _write_minimal_docx(path)

    def fail_document(_path: str):
        raise KeyError("word/media/image000.png")

    monkeypatch.setattr(
        attachment_module,
        "Document",
        fail_document,
    )

    result = extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".docx"},
    )

    assert result.status == "extracted"
    assert result.method == "ooxml-docx-fallback"
    assert "Восстановленный текст DOCX" in result.text
    assert result.metadata["fallback_used"] is True


def test_xlsx_fallback_marks_unresolved_shared_string(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-shared-strings.xlsx"
    _write_case_variant_xlsx(path)

    with zipfile.ZipFile(path, "r") as source:
        files = {
            name: source.read(name)
            for name in source.namelist()
            if name != "xl/SharedStrings.xml"
        }
    with zipfile.ZipFile(path, "w") as target:
        for name, payload in files.items():
            target.writestr(name, payload)

    result = extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".xlsx"},
    )

    assert result.status == "extracted"
    assert "неразрешённая строка XLSX" in result.text
    assert (
        result.metadata["unresolved_shared_strings"]
        == 1
    )
