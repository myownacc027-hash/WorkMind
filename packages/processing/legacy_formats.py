"""Read-only extraction from legacy Excel XLS and Visio Open XML VSDX.

No Office automation, macros, network I/O, or file writes are performed.
These functions are called after the shared attachment/document size gate.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from packages.processing.attachments import ExtractionResult

_MAX_XLS_CELLS = 1_000_000
_MAX_VSDX_PARTS = 4_000
_MAX_VSDX_XML_PART_BYTES = 12 * 1024 * 1024
_MAX_VSDX_TOTAL_XML_BYTES = 96 * 1024 * 1024
_MAX_VSDX_PAGES = 400
_MAX_VSDX_SHAPES = 100_000
_MAX_VSDX_CONNECTS = 100_000
_VISIO_PREFIX = "visio/pages/"
_RID_ATTRIBUTE = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _append_limited(
    parts: list[str], value: str, *, max_chars: int, current: int
) -> tuple[int, bool]:
    """Limit output length while retaining section boundaries."""
    if not value:
        return current, False
    sep = "\n" if parts else ""
    remaining = max_chars - current
    if remaining <= 0:
        return current, True
    addition = sep + value
    if len(addition) > remaining:
        parts.append(addition[:remaining])
        return max_chars, True
    parts.append(addition)
    return current + len(addition), False


def _result(
    *, text: str, method: str, metadata: dict, truncated: bool = False
) -> ExtractionResult:
    metadata["truncated"] = truncated
    return ExtractionResult(
        status="extracted" if text.strip() else "empty_no_text",
        text=text,
        method=method,
        metadata=metadata,
    )


def _column_label(number: int) -> str:
    number += 1
    label = ""
    while number:
        number, letter = divmod(number - 1, 26)
        label = chr(65 + letter) + label
    return label


def extract_xls(path: Path, max_chars: int) -> ExtractionResult:
    """Extract cached XLS cell values and coordinates, not executable formulas."""
    import xlrd

    chunks: list[str] = []
    current = 0
    limited = False
    visited = 0
    sheet_count = 0
    nonempty = 0
    try:
        book = xlrd.open_workbook(filename=str(path), on_demand=True)
    except xlrd.XLRDError as exc:
        message = str(exc)
        if any(word in message.casefold() for word in ("encrypt", "password", "protect")):
            return ExtractionResult(
                status="protected",
                text="",
                method="xlrd",
                metadata={"extension": ".xls"},
                error="Password-protected XLS is not supported.",
            )
        return ExtractionResult(
            status="corrupt",
            text="",
            method="xlrd",
            metadata={"extension": ".xls"},
            error=f"Invalid XLS workbook: {message}",
        )
    try:
        sheet_count = book.nsheets
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            title = f"[Лист: {sheet.name}]"
            current, limited = _append_limited(
                chunks, title, max_chars=max_chars, current=current
            )
            if limited:
                break
            for row_index in range(sheet.nrows):
                row = []
                row_length = min(sheet.row_len(row_index), sheet.ncols)
                for column in range(row_length):
                    if visited >= _MAX_XLS_CELLS:
                        limited = True
                        break
                    visited += 1
                    cell = sheet.cell(row_index, column)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            value = xlrd.xldate_as_datetime(
                                value, book.datemode
                            ).isoformat(sep=" ")
                        except xlrd.XLDateError:
                            value = str(value)
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = "TRUE" if value else "FALSE"
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(
                            value, f"Excel error {value}"
                        )
                    elif cell.ctype == xlrd.XL_CELL_NUMBER and float(value).is_integer():
                        value = str(int(value))
                    else:
                        value = str(value)
                    value = _normalize(str(value))
                    if not value:
                        continue
                    nonempty += 1
                    row.append(f"{_column_label(column)}{row_index + 1}: {value}")
                if row:
                    current, output_limited = _append_limited(
                        chunks, " | ".join(row), max_chars=max_chars, current=current
                    )
                    limited = limited or output_limited
                if limited:
                    break
            if limited:
                break
    finally:
        book.release_resources()
    return _result(
        text="".join(chunks),
        method="xlrd-xls",
        metadata={
            "sheet_count": sheet_count,
            "nonempty_cells": nonempty,
            "cells_scanned": visited,
            "cached_formula_values_only": True,
        },
        truncated=limited,
    )


def _read_xml(zf: zipfile.ZipFile, name: str, names: set[str]) -> ET.Element:
    if name not in names:
        raise ValueError(f"Missing Visio package part: {name}")
    info = zf.getinfo(name)
    if info.file_size > _MAX_VSDX_XML_PART_BYTES:
        raise ValueError(f"Visio XML part exceeds size limit: {name}")
    return ET.fromstring(zf.read(name))


def _page_part(target: str) -> str:
    if not target or "://" in target or "\\" in target:
        raise ValueError("Unsafe Visio page relationship target")
    if target.startswith("/"):
        name = posixpath.normpath(target.lstrip("/"))
    else:
        name = posixpath.normpath(posixpath.join("visio/pages", target))
    if not name.startswith(_VISIO_PREFIX) or not name.endswith(".xml"):
        raise ValueError("Invalid Visio page relationship target")
    return name


def _shape_fields(shape: ET.Element) -> tuple[str, list[str]]:
    text = ""
    props: list[str] = []
    for child in shape:
        tag = _local_name(child.tag)
        if tag == "Text":
            text = _normalize("".join(child.itertext()))
        elif tag == "Section" and child.get("N") == "Property":
            for row in child:
                if _local_name(row.tag) != "Row":
                    continue
                value = ""
                label = row.get("N") or row.get("IX") or "property"
                for cell in row:
                    if _local_name(cell.tag) == "Cell" and cell.get("N") == "Value":
                        value = cell.get("V") or ""
                        break
                if value.strip():
                    props.append(f"{label}={_normalize(value)}")
    return text, props


def _walk_shapes(root: ET.Element):
    """Yield top-level and group-member shapes without double counting."""
    stack = []
    for child in root:
        if _local_name(child.tag) == "Shapes":
            stack.extend((shape, None) for shape in reversed(list(child)))
    while stack:
        shape, parent = stack.pop()
        if _local_name(shape.tag) != "Shape":
            continue
        yield shape, parent
        for node in shape:
            if _local_name(node.tag) == "Shapes":
                stack.extend((sub, shape.get("ID")) for sub in reversed(list(node)))


def extract_vsdx(path: Path, max_chars: int) -> ExtractionResult:
    """Preserve Visio page order, shape identifiers/text and explicit connections.

    A Connect is represented as stored. The direction of a business process
    cannot be inferred from FromSheet/ToSheet geometry.
    """
    chunks: list[str] = []
    current = 0
    limited = False
    page_count = shape_count = connect_count = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_VSDX_PARTS:
                raise ValueError("Visio archive has too many parts")
            total_xml = sum(
                x.file_size
                for x in infos
                if x.filename.endswith((".xml", ".rels"))
            )
            if total_xml > _MAX_VSDX_TOTAL_XML_BYTES:
                raise ValueError("Visio XML content exceeds size limit")
            names = {info.filename for info in infos}
            index = _read_xml(archive, "visio/pages/pages.xml", names)
            relationships = _read_xml(
                archive, "visio/pages/_rels/pages.xml.rels", names
            )
            rels = {
                node.get("Id"): node.get("Target")
                for node in relationships
                if _local_name(node.tag) == "Relationship"
                and node.get("TargetMode", "Internal") != "External"
            }
            for page in index.iter():
                if _local_name(page.tag) != "Page":
                    continue
                page_count += 1
                if page_count > _MAX_VSDX_PAGES:
                    limited = True
                    break
                page_name = page.get("Name") or page.get("NameU") or f"Page {page_count}"
                page_id = page.get("ID") or "unknown"
                rel_id = next(
                    (
                        node.get(_RID_ATTRIBUTE)
                        for node in page
                        if _local_name(node.tag) == "Rel"
                    ),
                    None,
                )
                if not rel_id or rel_id not in rels:
                    raise ValueError(f"Visio page {page_id} has no valid relationship")
                part_name = _page_part(rels[rel_id] or "")
                document = _read_xml(archive, part_name, names)
                current, limited = _append_limited(
                    chunks,
                    f"[Страница Visio: {page_name}; ID={page_id}]",
                    max_chars=max_chars,
                    current=current,
                )
                if limited:
                    break
                for shape, parent_id in _walk_shapes(document):
                    shape_count += 1
                    if shape_count > _MAX_VSDX_SHAPES:
                        limited = True
                        break
                    shape_id = shape.get("ID") or "unknown"
                    shape_text, props = _shape_fields(shape)
                    shape_name = shape.get("NameU") or shape.get("Name") or ""
                    if not shape_text and not props and (
                        not shape_name
                        or re.fullmatch(r"Sheet\.\d+", shape_name, flags=re.I)
                    ):
                        continue
                    line = f"Фигура ID={shape_id}"
                    if shape_name:
                        line += f"; имя={shape_name}"
                    if parent_id:
                        line += f"; группа={parent_id}"
                    if shape.get("Master"):
                        line += f"; master={shape.get('Master')}"
                    if shape_text:
                        line += f"; текст={shape_text}"
                    if props:
                        line += "; свойства=" + "; ".join(props)
                    current, limited = _append_limited(
                        chunks, line, max_chars=max_chars, current=current
                    )
                    if limited:
                        break
                if limited:
                    break
                for connects in document:
                    if _local_name(connects.tag) != "Connects":
                        continue
                    for connection in connects:
                        if _local_name(connection.tag) != "Connect":
                            continue
                        connect_count += 1
                        if connect_count > _MAX_VSDX_CONNECTS:
                            limited = True
                            break
                        from_id = connection.get("FromSheet")
                        to_id = connection.get("ToSheet")
                        if not from_id or not to_id:
                            continue
                        line = f"Соединение Visio: FromSheet={from_id}, ToSheet={to_id}"
                        if connection.get("FromCell"):
                            line += f", FromCell={connection.get('FromCell')}"
                        if connection.get("ToCell"):
                            line += f", ToCell={connection.get('ToCell')}"
                        current, limited = _append_limited(
                            chunks, line, max_chars=max_chars, current=current
                        )
                        if limited:
                            break
                    if limited:
                        break
                if limited:
                    break
    except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError, KeyError) as exc:
        return ExtractionResult(
            status="corrupt",
            text="",
            method="vsdx-xml",
            metadata={"page_count": page_count},
            error=str(exc),
        )
    return _result(
        text="".join(chunks),
        method="vsdx-xml",
        metadata={
            "page_count": page_count,
            "shapes": shape_count,
            "connections": connect_count,
            "diagram_direction_inferred": False,
        },
        truncated=limited,
    )
