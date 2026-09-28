
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from packages.sources.bitrix24 import B24XlsxError
from packages.sources.bitrix24 import parse_b24_xlsx


def _save_book(
    path: Path,
    *,
    headers: list[str],
    rows: list[list[object]],
    task_url: str | None = None,
    parent_url: str | None = None,
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Tasks"

    worksheet.append(
        headers
    )

    for row in rows:
        worksheet.append(
            row
        )

    if task_url is not None:
        column = headers.index(
            "ID"
        ) + 1

        worksheet.cell(
            row=2,
            column=column,
        ).hyperlink = task_url

    if (
        parent_url is not None
        and "ID базовой задачи" in headers
    ):
        column = headers.index(
            "ID базовой задачи"
        ) + 1

        worksheet.cell(
            row=2,
            column=column,
        ).hyperlink = parent_url

    workbook.save(
        path
    )

    workbook.close()

    return path


def test_parser_reads_task_identity_and_project(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
            "Проект",
        ],
        rows=[
            [
                221516,
                "Test task",
                "Description",
                "ДСЛ. Операционные задачи",
            ],
        ],
        task_url=(
            "https://example.local/workgroups/group/49/"
            "tasks/task/view/221516/"
        ),
    )

    snapshot = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    )

    task = snapshot.tasks[0]

    assert task.external_id == "221516"
    assert task.canonical[
        "project_external_id"
    ] == "49"


def test_participants_are_split_by_commas(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Наблюдатели",
        ],
        rows=[
            [
                1,
                "Person One, Person Two, Queue: Role",
            ],
        ],
    )

    task = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert task.canonical[
        "observer_labels"
    ] == [
        "Person One",
        "Person Two",
        "Queue: Role",
    ]


def test_naive_datetime_uses_moscow_source_timezone(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Крайний срок",
        ],
        rows=[
            [
                1,
                datetime(
                    2026,
                    9,
                    18,
                    15,
                    0,
                    0,
                ),
            ],
        ],
    )

    task = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert (
        task.canonical[
            "deadline_at"
        ].isoformat()
        == "2026-09-18T12:00:00+00:00"
    )


def test_summary_row_without_id_is_skipped(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
            "Затрачено",
        ],
        rows=[
            [1, "Task", "01:00"],
            [None, "Сумма:", "87:00"],
        ],
    )

    snapshot = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    )

    assert snapshot.row_count == 2
    assert snapshot.task_count == 1
    assert snapshot.skipped_row_count == 1


def test_fractional_task_id_is_rejected(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
        ],
        rows=[
            [
                221516.5,
                "Invalid",
            ],
        ],
    )

    with pytest.raises(
        B24XlsxError,
        match="fractional",
    ):
        parse_b24_xlsx(
            path,
            source_timezone="Europe/Moscow",
        )


def test_duration_supports_more_than_24_hours(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Планируемая длительность",
            "Затрачено",
        ],
        rows=[
            [
                1,
                "87:00",
                "101:18",
            ],
        ],
    )

    task = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert task.canonical[
        "planned_duration_minutes"
    ] == 5220

    assert task.canonical[
        "spent_minutes"
    ] == 6078


def test_derived_counter_changes_only_raw_hash(
    tmp_path: Path,
) -> None:
    first = _save_book(
        tmp_path / "first.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
            "Дней с даты постановки",
        ],
        rows=[
            [
                1,
                "Task",
                "Same description",
                310,
            ],
        ],
    )

    second = _save_book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
            "Дней с даты постановки",
        ],
        rows=[
            [
                1,
                "Task",
                "Same description",
                311,
            ],
        ],
    )

    a = parse_b24_xlsx(
        first,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    b = parse_b24_xlsx(
        second,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert a.raw_hash != b.raw_hash
    assert a.canonical_hash == b.canonical_hash
    assert (
        a.extraction_input_hash
        == b.extraction_input_hash
    )


def test_status_change_does_not_force_semantic_reextraction(
    tmp_path: Path,
) -> None:
    first = _save_book(
        tmp_path / "first.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
            "Статус",
        ],
        rows=[
            [
                1,
                "Task",
                "Same description",
                "Waiting",
            ],
        ],
    )

    second = _save_book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
            "Статус",
        ],
        rows=[
            [
                1,
                "Task",
                "Same description",
                "Running",
            ],
        ],
    )

    a = parse_b24_xlsx(
        first,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    b = parse_b24_xlsx(
        second,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert a.raw_hash != b.raw_hash
    assert a.canonical_hash != b.canonical_hash
    assert (
        a.extraction_input_hash
        == b.extraction_input_hash
    )


def test_description_change_changes_extraction_hash(
    tmp_path: Path,
) -> None:
    first = _save_book(
        tmp_path / "first.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
        ],
        rows=[
            [
                1,
                "Task",
                "Old description",
            ],
        ],
    )

    second = _save_book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
        ],
        rows=[
            [
                1,
                "Task",
                "New description",
            ],
        ],
    )

    a = parse_b24_xlsx(
        first,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    b = parse_b24_xlsx(
        second,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert (
        a.extraction_input_hash
        != b.extraction_input_hash
    )


def test_absent_column_is_not_materialized(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
        ],
        rows=[
            [
                1,
                "Task",
            ],
        ],
    )

    task = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert "description" not in task.canonical
    assert "deadline_at" not in task.canonical


def test_present_empty_scalar_becomes_none(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
        ],
        rows=[
            [
                1,
                "Task",
                None,
            ],
        ],
    )

    task = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert "description" in task.canonical
    assert task.canonical[
        "description"
    ] is None


def test_parent_can_be_outside_snapshot(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
            "ID базовой задачи",
        ],
        rows=[
            [
                1,
                "Child",
                156051,
            ],
        ],
        parent_url=(
            "https://example.local/workgroups/group/49/"
            "tasks/task/view/156051/"
        ),
    )

    task = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).tasks[0]

    assert task.canonical[
        "parent_external_id"
    ] == "156051"


def test_duplicate_header_is_rejected(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
            "Название",
        ],
        rows=[
            [
                1,
                "One",
                "Two",
            ],
        ],
    )

    with pytest.raises(
        B24XlsxError,
        match="Duplicate XLSX headers",
    ):
        parse_b24_xlsx(
            path,
            source_timezone="Europe/Moscow",
        )


def test_hyperlink_id_must_match_cell_id(
    tmp_path: Path,
) -> None:
    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            "Название",
        ],
        rows=[
            [
                100,
                "Task",
            ],
        ],
        task_url=(
            "https://example.local/workgroups/group/49/"
            "tasks/task/view/101/"
        ),
    )

    with pytest.raises(
        B24XlsxError,
        match="hyperlink ID does not match",
    ):
        parse_b24_xlsx(
            path,
            source_timezone="Europe/Moscow",
        )


def test_audit_summary_distinguishes_raw_and_normalized_description_lengths(
    tmp_path: Path,
) -> None:
    description_header = (
        "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435"
    )

    path = _save_book(
        tmp_path / "tasks.xlsx",
        headers=[
            "ID",
            description_header,
        ],
        rows=[
            [
                1,
                "Test\u00a0",
            ],
        ],
    )

    summary = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    ).audit_summary()

    assert summary["description_count"] == 1
    assert summary["description_min"] == 5
    assert summary["description_max"] == 5

    assert summary["normalized_description_count"] == 1
    assert summary["normalized_description_min"] == 4
    assert summary["normalized_description_max"] == 4
