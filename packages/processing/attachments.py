from __future__ import annotations

import csv
import html
import json
import posixpath
import re
import warnings
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader
from pptx import Presentation


class AttachmentExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    status: str
    text: str
    method: str | None
    metadata: dict[str, Any]
    error: str | None = None


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".log",
}


def _normalize(value: str) -> str:
    value = value.replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                compact.append("")
            blank = True
            continue
        blank = False
        compact.append(line)
    return "\n".join(compact).strip()


def _limit(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


def _decode_text(data: bytes) -> tuple[str, str]:
    for encoding in (
        "utf-8-sig",
        "utf-16",
        "cp1251",
        "cp866",
    ):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def _extract_pdf(path: Path, max_chars: int) -> ExtractionResult:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
        except Exception as exc:
            return ExtractionResult(
                status="protected",
                text="",
                method="pypdf",
                metadata={},
                error=f"Encrypted PDF: {exc}",
            )
        if not decrypted:
            return ExtractionResult(
                status="protected",
                text="",
                method="pypdf",
                metadata={},
                error="Encrypted PDF requires a password.",
            )

    parts: list[str] = []
    truncated = False
    for number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            page_text = f"[Ошибка чтения страницы {number}: {exc}]"
        page_text = _normalize(page_text)
        if page_text:
            parts.append(f"[Страница {number}]\n{page_text}")
        current = "\n\n".join(parts)
        if len(current) >= max_chars:
            current, truncated = _limit(current, max_chars)
            parts = [current]
            break

    text = _normalize("\n\n".join(parts))
    if not text:
        return ExtractionResult(
            status="empty_no_text",
            text="",
            method="pypdf",
            metadata={
                "page_count": len(reader.pages),
                "ocr_required": True,
            },
        )

    return ExtractionResult(
        status="extracted",
        text=text,
        method="pypdf",
        metadata={
            "page_count": len(reader.pages),
            "truncated": truncated,
        },
    )


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PACKAGE_REL_NS = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)


def _zip_member_map(
    archive: zipfile.ZipFile,
) -> dict[str, str]:
    return {
        name.replace("\\", "/").lstrip("/").casefold(): name
        for name in archive.namelist()
    }


def _read_zip_member(
    archive: zipfile.ZipFile,
    members: dict[str, str],
    name: str,
) -> bytes:
    normalized = name.replace("\\", "/").lstrip("/")
    actual = members.get(normalized.casefold())
    if actual is None:
        raise KeyError(f"OOXML member not found: {normalized}")
    return archive.read(actual)


def _word_xml_lines(data: bytes) -> list[str]:
    root = ElementTree.fromstring(data)
    paragraph_tag = f"{{{_WORD_NS}}}p"
    text_tag = f"{{{_WORD_NS}}}t"
    tab_tag = f"{{{_WORD_NS}}}tab"
    break_tags = {
        f"{{{_WORD_NS}}}br",
        f"{{{_WORD_NS}}}cr",
    }

    lines: list[str] = []
    for paragraph in root.iter(paragraph_tag):
        pieces: list[str] = []
        for node in paragraph.iter():
            if node.tag == text_tag and node.text:
                pieces.append(node.text)
            elif node.tag == tab_tag:
                pieces.append("\t")
            elif node.tag in break_tags:
                pieces.append("\n")
        value = _normalize("".join(pieces))
        if value:
            lines.append(value)
    return lines


def _extract_docx_ooxml(
    path: Path,
    max_chars: int,
    *,
    primary_error: Exception,
) -> ExtractionResult:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = _zip_member_map(archive)
            candidates = [
                actual
                for folded, actual in members.items()
                if folded == "word/document.xml"
                or folded.startswith("word/header")
                or folded.startswith("word/footer")
                or folded in {
                    "word/footnotes.xml",
                    "word/endnotes.xml",
                    "word/comments.xml",
                }
            ]
            candidates.sort(
                key=lambda name: (
                    0 if name.casefold() == "word/document.xml" else 1,
                    name.casefold(),
                )
            )

            parts: list[str] = []
            read_parts = 0
            for member in candidates:
                try:
                    lines = _word_xml_lines(archive.read(member))
                except (KeyError, OSError, zipfile.BadZipFile):
                    continue
                if not lines:
                    continue
                read_parts += 1
                label = member.rsplit("/", 1)[-1]
                parts.append(f"[Часть DOCX: {label}]\n" + "\n".join(lines))

            text, truncated = _limit(
                _normalize("\n\n".join(parts)),
                max_chars,
            )
            if not text:
                return ExtractionResult(
                    status="empty_no_text",
                    text="",
                    method="ooxml-docx-fallback",
                    metadata={
                        "fallback_used": True,
                        "primary_error": str(primary_error),
                        "parts_read": read_parts,
                    },
                    error=(
                        "DOCX package was recovered, but no text "
                        "was found."
                    ),
                )

            return ExtractionResult(
                status="extracted",
                text=text,
                method="ooxml-docx-fallback",
                metadata={
                    "fallback_used": True,
                    "primary_error": str(primary_error),
                    "parts_read": read_parts,
                    "truncated": truncated,
                },
            )
    except Exception as fallback_error:
        return ExtractionResult(
            status="corrupt",
            text="",
            method="ooxml-docx-fallback",
            metadata={
                "fallback_used": True,
                "primary_error": str(primary_error),
                "fallback_error": str(fallback_error),
            },
            error=(
                "DOCX text could not be recovered: "
                f"{fallback_error}"
            ),
        )


def _extract_docx(path: Path, max_chars: int) -> ExtractionResult:
    try:
        document = Document(str(path))
        parts: list[str] = []

        paragraphs = [
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]
        parts.extend(paragraphs)

        for table_number, table in enumerate(
            document.tables,
            start=1,
        ):
            table_lines = [f"[Таблица {table_number}]"]
            for row in table.rows:
                values = [
                    _normalize(cell.text).replace("\n", " | ")
                    for cell in row.cells
                ]
                if any(values):
                    table_lines.append("\t".join(values))
            if len(table_lines) > 1:
                parts.append("\n".join(table_lines))

        text, truncated = _limit(
            _normalize("\n\n".join(parts)),
            max_chars,
        )
        return ExtractionResult(
            status="extracted" if text else "empty_no_text",
            text=text,
            method="python-docx",
            metadata={
                "paragraph_count": len(document.paragraphs),
                "table_count": len(document.tables),
                "truncated": truncated,
                "fallback_used": False,
            },
        )
    except Exception as primary_error:
        return _extract_docx_ooxml(
            path,
            max_chars,
            primary_error=primary_error,
        )

def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _resolve_relationship_target(
    base_directory: str,
    target: str,
) -> str:
    normalized = target.replace("\\", "/")
    if normalized.startswith("/"):
        return normalized.lstrip("/")
    return posixpath.normpath(
        posixpath.join(base_directory, normalized)
    )


def _shared_strings_from_archive(
    archive: zipfile.ZipFile,
    members: dict[str, str],
) -> list[str]:
    actual = members.get("xl/sharedstrings.xml")
    if actual is None:
        actual = next(
            (
                name
                for folded, name in members.items()
                if folded.endswith("/sharedstrings.xml")
            ),
            None,
        )
    if actual is None:
        return []

    root = ElementTree.fromstring(archive.read(actual))
    item_tag = f"{{{_SPREADSHEET_NS}}}si"
    text_tag = f"{{{_SPREADSHEET_NS}}}t"
    values: list[str] = []
    for item in root.iter(item_tag):
        values.append(
            "".join(
                node.text or ""
                for node in item.iter(text_tag)
            )
        )
    return values


def _sheet_entries(
    archive: zipfile.ZipFile,
    members: dict[str, str],
) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(
        _read_zip_member(
            archive,
            members,
            "xl/workbook.xml",
        )
    )

    relationships: dict[str, str] = {}
    try:
        rel_root = ElementTree.fromstring(
            _read_zip_member(
                archive,
                members,
                "xl/_rels/workbook.xml.rels",
            )
        )
        relationship_tag = f"{{{_PACKAGE_REL_NS}}}Relationship"
        for rel in rel_root.iter(relationship_tag):
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                relationships[rel_id] = _resolve_relationship_target(
                    "xl",
                    target,
                )
    except (KeyError, ElementTree.ParseError):
        relationships = {}

    sheet_tag = f"{{{_SPREADSHEET_NS}}}sheet"
    relationship_id = f"{{{_OFFICE_REL_NS}}}id"
    entries: list[tuple[str, str]] = []

    for sheet in workbook.iter(sheet_tag):
        title = sheet.attrib.get("name") or "Лист"
        rel_id = sheet.attrib.get(relationship_id)
        target = relationships.get(rel_id or "")
        if target and target.casefold() in members:
            entries.append(
                (title, members[target.casefold()])
            )

    if entries:
        return entries

    worksheet_members = sorted(
        actual
        for folded, actual in members.items()
        if folded.startswith("xl/worksheets/")
        and folded.endswith(".xml")
    )
    return [
        (Path(name).stem, name)
        for name in worksheet_members
    ]


def _spreadsheet_cell_value(
    cell: ElementTree.Element,
    shared_strings: list[str],
) -> tuple[str, bool]:
    cell_type = cell.attrib.get("t")
    value_tag = f"{{{_SPREADSHEET_NS}}}v"
    formula_tag = f"{{{_SPREADSHEET_NS}}}f"
    text_tag = f"{{{_SPREADSHEET_NS}}}t"

    value_node = cell.find(value_tag)
    formula_node = cell.find(formula_tag)
    raw_value = (
        value_node.text
        if value_node is not None and value_node.text is not None
        else ""
    )
    formula = (
        formula_node.text
        if formula_node is not None
        and formula_node.text is not None
        else ""
    )

    unresolved_shared_string = False
    if cell_type == "inlineStr":
        value = "".join(
            node.text or ""
            for node in cell.iter(text_tag)
        )
    elif cell_type == "s":
        try:
            index = int(raw_value)
            value = shared_strings[index]
        except (ValueError, IndexError):
            value = (
                f"[неразрешённая строка XLSX #{raw_value}]"
                if raw_value
                else "[неразрешённая строка XLSX]"
            )
            unresolved_shared_string = True
    elif cell_type == "b":
        value = "TRUE" if raw_value == "1" else "FALSE"
    elif cell_type == "e":
        value = f"#ERROR:{raw_value}"
    else:
        value = raw_value

    if formula:
        if value:
            value = f"={formula} -> {value}"
        else:
            value = f"={formula}"

    return value.strip(), unresolved_shared_string


def _extract_xlsx_ooxml(
    path: Path,
    max_chars: int,
    *,
    primary_error: Exception,
) -> ExtractionResult:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = _zip_member_map(archive)
            shared_strings = _shared_strings_from_archive(
                archive,
                members,
            )
            sheets = _sheet_entries(archive, members)

            parts: list[str] = []
            rows_seen = 0
            unresolved = 0
            truncated = False

            row_tag = f"{{{_SPREADSHEET_NS}}}row"
            cell_tag = f"{{{_SPREADSHEET_NS}}}c"

            for title, member in sheets:
                root = ElementTree.fromstring(archive.read(member))
                sheet_lines: list[str] = []
                sheet_lines_chars = 0
                header = f"[Лист: {title}]\n"
                prefix_chars = len("\n\n".join(parts)) + (
                    2 if parts else 0
                )
                for row in root.iter(row_tag):
                    row_number = row.attrib.get("r") or "?"
                    cells: list[str] = []
                    for cell in row.iter(cell_tag):
                        coordinate = cell.attrib.get("r") or "?"
                        value, unresolved_flag = (
                            _spreadsheet_cell_value(
                                cell,
                                shared_strings,
                            )
                        )
                        if unresolved_flag:
                            unresolved += 1
                        if value:
                            cells.append(
                                f"{coordinate}={value}"
                            )

                    if not cells:
                        continue
                    rows_seen += 1
                    line = f"{row_number}\t" + "\t".join(cells)
                    sheet_lines_chars += len(line) + (
                        1 if sheet_lines else 0
                    )
                    sheet_lines.append(line)

                    # Avoid reconstructing all preceding rows for each row.
                    if (
                        prefix_chars
                        + len(header)
                        + sheet_lines_chars
                        >= max_chars
                    ):
                        truncated = True
                        break

                if sheet_lines:
                    parts.append(header + "\n".join(sheet_lines))
                if truncated:
                    break

            text, was_limited = _limit(
                _normalize("\n\n".join(parts)),
                max_chars,
            )
            truncated = truncated or was_limited

            return ExtractionResult(
                status="extracted" if text else "empty_no_text",
                text=text,
                method="ooxml-xlsx-fallback",
                metadata={
                    "fallback_used": True,
                    "primary_error": str(primary_error),
                    "sheet_count": len(sheets),
                    "rows_with_values": rows_seen,
                    "shared_strings_found": len(shared_strings),
                    "unresolved_shared_strings": unresolved,
                    "truncated": truncated,
                },
                error=(
                    None
                    if text
                    else "XLSX package contains no readable cell values."
                ),
            )
    except Exception as fallback_error:
        return ExtractionResult(
            status="corrupt",
            text="",
            method="ooxml-xlsx-fallback",
            metadata={
                "fallback_used": True,
                "primary_error": str(primary_error),
                "fallback_error": str(fallback_error),
            },
            error=(
                "XLSX text could not be recovered: "
                f"{fallback_error}"
            ),
        )


def _extract_xlsx(path: Path, max_chars: int) -> ExtractionResult:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=(
                    "Unknown extension is not supported "
                    "and will be removed"
                ),
                category=UserWarning,
                module=r"openpyxl\..*",
            )
            warnings.filterwarnings(
                "ignore",
                message=(
                    "Conditional Formatting extension "
                    "is not supported and will be removed"
                ),
                category=UserWarning,
                module=r"openpyxl\..*",
            )

            # CAS objects are named by SHA-256 and have no extension.
            # openpyxl rejects those *paths* even when their XLSX contents
            # are valid. A binary file handle bypasses the filename check.
            # Keep it open until the read-only workbook has been consumed.
            with path.open("rb") as source:
                workbook = load_workbook(
                    filename=source,
                    read_only=True,
                    data_only=False,
                )
                parts: list[str] = []
                rows_seen = 0
                truncated = False
                sheet_count = len(workbook.sheetnames)
                # Exact length of "\n".join(parts) while the workbook grows.
                parts_chars = 0

                try:
                    for worksheet in workbook.worksheets:
                        sheet_lines = [f"[Лист: {worksheet.title}]"]
                        sheet_chars = len(sheet_lines[0])
                        for row_number, row in enumerate(
                            worksheet.iter_rows(values_only=True),
                            start=1,
                        ):
                            values = [
                                _cell_text(value)
                                for value in row
                            ]
                            while values and not values[-1]:
                                values.pop()
                            if not any(values):
                                continue
                            rows_seen += 1
                            line = f"{row_number}\t" + "\t".join(values)
                            sheet_chars += 1 + len(line)
                            sheet_lines.append(line)

                            # Length of "\n".join(parts + sheet_lines).
                            # Do not join the full accumulated text per row.
                            if (
                                parts_chars
                                + (1 if parts else 0)
                                + sheet_chars
                                >= max_chars
                            ):
                                truncated = True
                                break

                        sheet_text = "\n".join(sheet_lines)
                        parts_chars += len(sheet_text) + (
                            1 if parts else 0
                        )
                        parts.append(sheet_text)
                        if truncated:
                            break
                finally:
                    workbook.close()

        text, was_limited = _limit(
            _normalize("\n\n".join(parts)),
            max_chars,
        )
        truncated = truncated or was_limited
        return ExtractionResult(
            status="extracted" if text else "empty_no_text",
            text=text,
            method="openpyxl",
            metadata={
                "sheet_count": sheet_count,
                "rows_with_values": rows_seen,
                "truncated": truncated,
                "fallback_used": False,
            },
        )
    except Exception as primary_error:
        return _extract_xlsx_ooxml(
            path,
            max_chars,
            primary_error=primary_error,
        )

def _extract_pptx(path: Path, max_chars: int) -> ExtractionResult:
    presentation = Presentation(str(path))
    parts: list[str] = []
    truncated = False
    note_count = 0
    table_count = 0

    for slide_number, slide in enumerate(
        presentation.slides,
        start=1,
    ):
        lines: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                value = _normalize(shape.text or "")
                if value:
                    lines.append(value)
            if getattr(shape, "has_table", False):
                table_count += 1
                for row in shape.table.rows:
                    values = [
                        _normalize(cell.text or "").replace("\n", " | ")
                        for cell in row.cells
                    ]
                    if any(values):
                        lines.append("\t".join(values))

        try:
            notes_text = _normalize(
                slide.notes_slide.notes_text_frame.text or ""
            )
        except Exception:
            notes_text = ""
        if notes_text:
            note_count += 1
            lines.append(f"[Заметки]\n{notes_text}")

        if lines:
            parts.append(
                f"[Слайд {slide_number}]\n" + "\n".join(lines)
            )
        candidate = "\n\n".join(parts)
        if len(candidate) >= max_chars:
            truncated = True
            break

    text, was_limited = _limit(
        _normalize("\n\n".join(parts)),
        max_chars,
    )
    return ExtractionResult(
        status="extracted" if text else "empty_no_text",
        text=text,
        method="python-pptx",
        metadata={
            "slide_count": len(presentation.slides),
            "slides_with_notes": note_count,
            "table_count": table_count,
            "truncated": truncated or was_limited,
        },
    )

def _extract_text_file(
    path: Path,
    extension: str,
    max_chars: int,
) -> ExtractionResult:
    raw = path.read_bytes()
    decoded, encoding = _decode_text(raw)

    method = f"text:{encoding}"
    if extension == ".json":
        try:
            parsed = json.loads(decoded)
            decoded = json.dumps(
                parsed,
                ensure_ascii=False,
                indent=2,
            )
            method = f"json:{encoding}"
        except json.JSONDecodeError:
            pass
    elif extension == ".xml":
        try:
            root = ElementTree.fromstring(decoded)
            decoded = " ".join(
                text.strip()
                for text in root.itertext()
                if text.strip()
            )
            method = f"xml:{encoding}"
        except ElementTree.ParseError:
            pass
    elif extension in {".html", ".htm"}:
        decoded = re.sub(
            r"(?is)<(script|style).*?>.*?</\1>",
            " ",
            decoded,
        )
        decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
        decoded = html.unescape(decoded)
        method = f"html:{encoding}"
    elif extension == ".csv":
        try:
            rows = csv.reader(decoded.splitlines())
            decoded = "\n".join(
                "\t".join(cell.strip() for cell in row)
                for row in rows
            )
            method = f"csv:{encoding}"
        except csv.Error:
            pass

    text, truncated = _limit(_normalize(decoded), max_chars)
    return ExtractionResult(
        status="extracted" if text else "empty_no_text",
        text=text,
        method=method,
        metadata={
            "encoding": encoding,
            "truncated": truncated,
        },
    )


def extract_attachment(
    path: Path,
    *,
    max_bytes: int,
    max_chars: int,
    supported_extensions: set[str],
    filename_hint: str | None = None,
) -> ExtractionResult:
    extension = (
        Path(filename_hint).suffix.lower()
        if filename_hint is not None
        else path.suffix.lower()
    )
    display_name = filename_hint or path.name

    if extension not in supported_extensions:
        return ExtractionResult(
            status="unsupported",
            text="",
            method=None,
            metadata={"extension": extension},
            error=f"Unsupported extension: {extension or '(none)'}",
        )

    if not path.exists() or not path.is_file():
        return ExtractionResult(
            status="missing",
            text="",
            method=None,
            metadata={},
            error=f"Attachment file not found: {path}",
        )

    size = path.stat().st_size
    if size > max_bytes:
        return ExtractionResult(
            status="too_large",
            text="",
            method=None,
            metadata={
                "size_bytes": size,
                "max_bytes": max_bytes,
            },
            error="Attachment exceeds extraction size limit.",
        )

    try:
        if extension in {".xls", ".vsdx"}:
            from packages.processing.legacy_formats import (
                extract_vsdx,
                extract_xls,
            )

            return (
                extract_xls(path, max_chars)
                if extension == ".xls"
                else extract_vsdx(path, max_chars)
            )
        if extension == ".pdf":
            return _extract_pdf(path, max_chars)
        if extension == ".docx":
            return _extract_docx(path, max_chars)
        if extension in {".xlsx", ".xlsm"}:
            return _extract_xlsx(path, max_chars)
        if extension == ".pptx":
            return _extract_pptx(path, max_chars)
        if extension in TEXT_EXTENSIONS:
            return _extract_text_file(
                path,
                extension,
                max_chars,
            )
    except Exception as exc:
        raise AttachmentExtractionError(
            f"Could not extract {display_name}: {exc}"
        ) from exc

    return ExtractionResult(
        status="unsupported",
        text="",
        method=None,
        metadata={"extension": extension},
        error=f"Unsupported extension: {extension}",
    )


def extract_document(
    path: Path,
    *,
    max_bytes: int,
    max_chars: int,
    supported_extensions: set[str],
) -> ExtractionResult:
    """Generic file extractor used by attachments and external documents."""
    return extract_attachment(
        path,
        max_bytes=max_bytes,
        max_chars=max_chars,
        supported_extensions=supported_extensions,
    )
