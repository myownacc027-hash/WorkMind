from __future__ import annotations

import hashlib
from pathlib import Path

from openpyxl import Workbook

from packages.processing import attachments


def _cas_workbook(tmp_path: Path, *, rows: int = 3) -> Path:
    source = tmp_path / "original.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    sheet.append(["Item", "Value"])
    for index in range(1, rows + 1):
        sheet.append([f"item-{index}", f"amount-{index}"])
    workbook.save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cas_path = tmp_path / digest
    source.rename(cas_path)
    return cas_path


def test_cas_xlsx_uses_primary_openpyxl_extractor(tmp_path: Path) -> None:
    cas_path = _cas_workbook(tmp_path)
    result = attachments.extract_attachment(
        cas_path,
        filename_hint="report.xlsx",
        supported_extensions={".xlsx"},
        max_bytes=1024 * 1024,
        max_chars=100_000,
    )
    assert result.status == "extracted"
    assert result.method == "openpyxl"
    assert result.metadata["fallback_used"] is False
    assert result.metadata["truncated"] is False
    assert "item-1" in result.text
    assert "amount-3" in result.text


def test_cas_xlsx_truncation_keeps_bounded_text(tmp_path: Path) -> None:
    cas_path = _cas_workbook(tmp_path, rows=2000)
    limit = 10_000
    result = attachments.extract_attachment(
        cas_path,
        filename_hint="large.xlsx",
        supported_extensions={".xlsx"},
        max_bytes=1024 * 1024,
        max_chars=limit,
    )
    assert result.method == "openpyxl"
    assert result.status == "extracted"
    assert result.metadata["truncated"] is True
    assert len(result.text) <= limit
    assert "item-1" in result.text
    assert result.metadata["rows_with_values"] < 2001


def test_fallback_remains_available_for_read_failures(
    tmp_path: Path, monkeypatch
) -> None:
    cas_path = _cas_workbook(tmp_path)

    def cannot_open(*args, **kwargs):
        raise ValueError("simulated primary parser failure")

    monkeypatch.setattr(attachments, "load_workbook", cannot_open)
    result = attachments.extract_attachment(
        cas_path,
        filename_hint="report.xlsx",
        supported_extensions={".xlsx"},
        max_bytes=1024 * 1024,
        max_chars=100_000,
    )
    assert result.status == "extracted"
    assert result.method == "ooxml-xlsx-fallback"
    assert result.metadata["fallback_used"] is True
    assert "simulated primary parser failure" in result.metadata["primary_error"]
    assert "item-2" in result.text
