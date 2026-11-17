from __future__ import annotations

import sys
import types
import zipfile
from pathlib import Path

import pytest

from apps.attachment_processor.backfill import _filter_values
from packages.common.config import Settings
from packages.processing.attachments import extract_attachment
from packages.processing.legacy_formats import _page_part

_VISIO_NS = "http://schemas.microsoft.com/office/visio/2012/main"
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _create_vsdx(path: Path, *, target: str = "page1.xml", invalid_xml: bool = False) -> None:
    pages = (
        f'<Pages xmlns="{_VISIO_NS}" xmlns:r="{_OFFICE_REL_NS}">'
        '<Page ID="0" Name="Приёмка"><Rel r:id="rId1"/></Page></Pages>'
    )
    relations = (
        f'<Relationships xmlns="{_RELS_NS}">'
        f'<Relationship Id="rId1" Target="{target}"/></Relationships>'
    )
    shape_xml = (
        f'<PageContents xmlns="{_VISIO_NS}">'
        '<Shapes><Shape ID="1" NameU="Operation"><Text>Проверка КИЗ</Text>'
        '<Section N="Property"><Row N="System">'
        '<Cell N="Value" V="WMS"/></Row></Section>'
        '<Shapes><Shape ID="3"><Text>ТСД</Text></Shape></Shapes>'
        '</Shape><Shape ID="2"><Text>Сверка ERP</Text></Shape></Shapes>'
        '<Connects><Connect FromSheet="1" ToSheet="2"'
        ' FromCell="BeginX" ToCell="PinX"/></Connects>'
        '</PageContents>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("visio/pages/pages.xml", pages)
        archive.writestr("visio/pages/_rels/pages.xml.rels", relations)
        archive.writestr(
            "visio/pages/page1.xml",
            "<broken" if invalid_xml else shape_xml,
        )


def _extract(path: Path, filename_hint: str | None = None, max_chars: int = 100_000):
    return extract_attachment(
        path,
        max_bytes=10_000_000,
        max_chars=max_chars,
        supported_extensions={".xls", ".vsdx"},
        filename_hint=filename_hint,
    )


def test_vsdx_pages_nested_shapes_properties_and_connections(tmp_path: Path) -> None:
    path = tmp_path / "process.vsdx"
    _create_vsdx(path)
    result = _extract(path)
    assert result.status == "extracted"
    assert result.method == "vsdx-xml"
    assert "Страница Visio: Приёмка" in result.text
    assert "Проверка КИЗ" in result.text
    assert "группа=1" in result.text
    assert "ТСД" in result.text
    assert "System=WMS" in result.text
    assert "FromSheet=1, ToSheet=2" in result.text
    assert result.metadata["page_count"] == 1
    assert result.metadata["shapes"] == 3
    assert result.metadata["connections"] == 1
    assert result.metadata["diagram_direction_inferred"] is False


def test_vsdx_corrupt_and_unsafe_relationship(tmp_path: Path) -> None:
    path = tmp_path / "process.vsdx"
    _create_vsdx(path, invalid_xml=True)
    assert _extract(path).status == "corrupt"
    _create_vsdx(path, target="../../outside.xml")
    assert _extract(path).status == "corrupt"
    with pytest.raises(ValueError):
        _page_part("https://example.com/remote.xml")


def test_vsdx_truncation(tmp_path: Path) -> None:
    path = tmp_path / "process.vsdx"
    _create_vsdx(path)
    result = _extract(path, max_chars=50)
    assert result.status == "extracted"
    assert len(result.text) <= 50
    assert result.metadata["truncated"] is True


def test_xls_with_cached_values_and_cell_locations(tmp_path: Path) -> None:
    class Cell:
        def __init__(self, kind, value):
            self.ctype = kind
            self.value = value

    class Sheet:
        name = "Остатки"
        nrows = 2
        ncols = 2

        def row_len(self, row):
            return 2

        def cell(self, row, col):
            return (
                ((Cell(1, "Код"), Cell(1, "Остаток")),
                 (Cell(1, "Товар"), Cell(2, 7.0)))
            )[row][col]

    class Book:
        nsheets = 1
        datemode = 0

        def sheet_by_index(self, index):
            return Sheet()

        def release_resources(self):
            pass

    xlrd = types.ModuleType("xlrd")
    xlrd.open_workbook = lambda **kw: Book()
    xlrd.XLRDError = ValueError
    xlrd.XLDateError = ValueError
    xlrd.XL_CELL_EMPTY = 0
    xlrd.XL_CELL_BLANK = 6
    xlrd.XL_CELL_NUMBER = 2
    xlrd.XL_CELL_DATE = 3
    xlrd.XL_CELL_BOOLEAN = 4
    xlrd.XL_CELL_ERROR = 5
    xlrd.error_text_from_code = {}
    xlrd.xldate_as_datetime = lambda *args: None

    path = tmp_path / "data.bin"
    path.write_bytes(b"not used by mocked xlrd")
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(sys.modules, "xlrd", xlrd)
        result = _extract(path, filename_hint="data.xls")
    assert result.status == "extracted"
    assert result.method == "xlrd-xls"
    assert "[Лист: Остатки]" in result.text
    assert "A2: Товар" in result.text
    assert "B2: 7" in result.text
    assert result.metadata["nonempty_cells"] == 4
    assert result.metadata["cached_formula_values_only"] is True


def test_configuration_and_backfill_filter_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Production .env can intentionally override extension defaults.
    # Test the defaults independently of a developer's local settings.
    monkeypatch.delenv("ATTACHMENT_SUPPORTED_EXTENSIONS", raising=False)
    monkeypatch.delenv("DOCUMENT_SUPPORTED_EXTENSIONS", raising=False)
    settings = Settings(_env_file=None)
    assert {".xls", ".vsdx"}.issubset(settings.attachment_supported_extensions)
    assert {".xls", ".vsdx"}.issubset(settings.document_supported_extensions)

    # Explicit configuration is authoritative, even if it omits new formats.
    monkeypatch.setenv("ATTACHMENT_SUPPORTED_EXTENSIONS", ".pdf,.docx")
    monkeypatch.setenv("DOCUMENT_SUPPORTED_EXTENSIONS", ".pdf,.docx")
    overridden = Settings(_env_file=None)
    assert ".xls" not in overridden.attachment_supported_extensions
    assert ".vsdx" not in overridden.document_supported_extensions

    assert _filter_values(".XLS, .vsdx,.xls") == {".xls", ".vsdx"}
    assert _filter_values("") == set()
