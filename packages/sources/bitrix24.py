
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from openpyxl import load_workbook


CONTRACT_VERSION = "b24-source-v1"
NORMALIZER_VERSION = "b24-normalizer-v1"

EXPECTED_AUDIT_HEADERS = (
    "ID",
    "Название",
    "Описание",
    "Последняя активность",
    "Крайний срок",
    "Постановщик",
    "Исполнитель",
    "Соисполнители",
    "Наблюдатели",
    "Статус",
    "Проект",
    "Дата создания",
    "Дата начала работы",
    "Дата изменения",
    "Дата закрытия",
    "Планируемая длительность",
    "Учитывать рабочее время",
    "Затрачено",
    "Теги",
    "Стадия",
    "Лид",
    "Контакт",
    "Компания",
    "Сделка",
    "CRM",
    "ID базовой задачи",
    "Название базовой задачи",
    "Поток",
    "Приоритет",
    "Номер заявки в helpdesk",
    "% Готовности",
    "Направление бизнеса",
    "Тип заявки",
    "Плановая дата",
    "Вид задачи",
    "Запуск формы",
    "Продукт",
    "Задача стандарта",
    "Бизнес-процесс",
    "Объект ГК МИ",
    "Плановая дата релиза",
    "Фактическая дата релиза",
    "Запрос переноса сроков",
    "В процессе смена крайнего срока",
    "Время для согласования переноса крайнего срока",
    "Причина переноса крайнего срока",
    "Использовался ИИ",
    "Комментарий к согласованию",
    "Итог согласования",
    "Кол-во переносов сроков",
    "Дополнительные сотрудники",
    "Дней с даты постановки",
    "Создано",
    "Дней ждет контроля",
    "Стадия базовой задачи",
)

TEXT_FIELDS = {
    "Название": "title",
    "Описание": "description",
    "Постановщик": "creator_label",
    "Исполнитель": "assignee_label",
    "Статус": "status",
    "Проект": "project_name",
    "Стадия": "stage",
    "Название базовой задачи": "parent_title_snapshot",
    "Направление бизнеса": "business_direction",
    "Тип заявки": "request_type",
    "Вид задачи": "task_kind",
    "Продукт": "product",
    "Причина переноса крайнего срока": "deadline_change_reason",
    "Комментарий к согласованию": "approval_comment",
    "Итог согласования": "approval_result",
    "Стадия базовой задачи": "parent_stage_snapshot",
    "Номер заявки в helpdesk": "helpdesk_number",
}

OPAQUE_TEXT_FIELDS = {
    "CRM": "crm_context_raw",
    "Поток": "flow_raw",
    "Приоритет": "priority_raw",
    "Запрос переноса сроков": "deadline_change_request_raw",
}

DATETIME_FIELDS = {
    "Последняя активность": "last_activity_at",
    "Крайний срок": "deadline_at",
    "Дата создания": "created_at_source",
    "Дата начала работы": "started_at",
    "Дата изменения": "updated_at_source",
    "Дата закрытия": "closed_at",
    "Плановая дата": "planned_at",
    "Плановая дата релиза": "planned_release_at",
    "Фактическая дата релиза": "actual_release_at",
}

DURATION_FIELDS = {
    "Планируемая длительность": "planned_duration_minutes",
    "Затрачено": "spent_minutes",
}

BOOLEAN_FIELDS = {
    "Учитывать рабочее время": "track_work_time",
    "В процессе смена крайнего срока": "deadline_change_in_progress",
    "Использовался ИИ": "ai_used",
}

INTEGER_FIELDS = {
    "Кол-во переносов сроков": "deadline_change_count",
}

FLOAT_FIELDS = {
    "% Готовности": "progress_percent",
}

PARTICIPANT_LIST_FIELDS = {
    "Соисполнители": "coassignee_labels",
    "Наблюдатели": "observer_labels",
    "Дополнительные сотрудники": "additional_employee_labels",
}

TAG_FIELDS = {
    "Теги": "tags",
}

REFERENCE_FIELDS = {
    "ID базовой задачи": "parent_external_id",
}

EXTRACTION_INPUT_FIELDS = (
    "external_id",
    "title",
    "description",
    "parent_external_id",
    "parent_title_snapshot",
    "project_external_id",
    "project_name",
    "business_direction",
    "request_type",
    "product",
)

TASK_URL_RE = re.compile(
    r"/tasks/task/view/(?P<task_id>\d+)(?:/|$)",
    re.IGNORECASE,
)

GROUP_URL_RE = re.compile(
    r"/workgroups/group/(?P<group_id>\d+)/",
    re.IGNORECASE,
)


class B24XlsxError(ValueError):
    pass


@dataclass(frozen=True)
class B24TaskRecord:
    row_number: int
    external_id: str
    present_columns: tuple[str, ...]
    raw_row: dict[str, Any]
    raw_hyperlinks: dict[str, str]
    canonical: dict[str, Any]
    canonical_json: dict[str, Any]
    extraction_input: dict[str, Any]
    raw_hash: str
    canonical_hash: str
    extraction_input_hash: str


@dataclass(frozen=True)
class B24SkippedRow:
    row_number: int
    reason: str
    raw_row: dict[str, Any]


@dataclass(frozen=True)
class B24WorkbookSnapshot:
    source_path: Path
    source_hash: str
    sheet_name: str
    source_timezone: str
    present_columns: tuple[str, ...]
    row_count: int
    task_count: int
    skipped_rows: tuple[B24SkippedRow, ...]
    hyperlink_count: int
    formula_count: int
    tasks: tuple[B24TaskRecord, ...]

    @property
    def skipped_row_count(self) -> int:
        return len(self.skipped_rows)

    def task_by_external_id(
        self,
        external_id: str,
    ) -> B24TaskRecord | None:
        value = str(external_id)

        for task in self.tasks:
            if task.external_id == value:
                return task

        return None

    def audit_summary(self) -> dict[str, Any]:
        parent_ids = [
            task.canonical.get("parent_external_id")
            for task in self.tasks
            if task.canonical.get("parent_external_id")
        ]

        task_ids = {
            task.external_id
            for task in self.tasks
        }

        distinct_parent_ids = set(parent_ids)

        raw_descriptions = [
            str(task.raw_row.get("\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435") or "")
            for task in self.tasks
            if not _is_empty(
                task.raw_row.get(
                    "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435"
                )
            )
        ]

        normalized_descriptions = [
            str(task.canonical.get("description") or "")
            for task in self.tasks
            if task.canonical.get("description")
        ]

        empty_columns = []

        for header in self.present_columns:
            values = [
                task.raw_row.get(header)
                for task in self.tasks
            ]

            if all(_is_empty(value) for value in values):
                empty_columns.append(header)

        project_mappings = sorted(
            {
                (
                    str(task.canonical.get("project_external_id")),
                    str(task.canonical.get("project_name")),
                )
                for task in self.tasks
                if task.canonical.get("project_external_id")
                and task.canonical.get("project_name")
            }
        )

        return {
            "source_path": str(self.source_path),
            "source_hash": self.source_hash,
            "sheet_name": self.sheet_name,
            "source_timezone": self.source_timezone,
            "physical_column_count": len(self.present_columns),
            "row_count": self.row_count,
            "task_count": self.task_count,
            "skipped_row_count": self.skipped_row_count,
            "unique_task_id_count": len(task_ids),
            "hyperlink_count": self.hyperlink_count,
            "formula_count": self.formula_count,
            "parent_task_count": len(parent_ids),
            "distinct_parent_id_count": len(distinct_parent_ids),
            "parents_present_in_snapshot": sorted(
                distinct_parent_ids & task_ids,
                key=_id_sort_key,
            ),
            "parents_absent_from_snapshot": sorted(
                distinct_parent_ids - task_ids,
                key=_id_sort_key,
            ),
            "description_count": len(raw_descriptions),
            "description_min": (
                min(map(len, raw_descriptions))
                if raw_descriptions
                else 0
            ),
            "description_median": (
                int(median(map(len, raw_descriptions)))
                if raw_descriptions
                else 0
            ),
            "description_max": (
                max(map(len, raw_descriptions))
                if raw_descriptions
                else 0
            ),
            "normalized_description_count": len(
                normalized_descriptions
            ),
            "normalized_description_min": (
                min(map(len, normalized_descriptions))
                if normalized_descriptions
                else 0
            ),
            "normalized_description_median": (
                int(
                    median(
                        map(
                            len,
                            normalized_descriptions,
                        )
                    )
                )
                if normalized_descriptions
                else 0
            ),
            "normalized_description_max": (
                max(map(len, normalized_descriptions))
                if normalized_descriptions
                else 0
            ),
            "empty_column_count": len(empty_columns),
            "empty_columns": empty_columns,
            "project_mappings": [
                {
                    "project_external_id": project_id,
                    "project_name": project_name,
                }
                for project_id, project_name in project_mappings
            ],
        }


def _id_sort_key(value: str) -> tuple[int, str]:
    text = str(value)

    if text.isdigit():
        return (0, f"{int(text):020d}")

    return (1, text)


def _is_empty(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and not value.strip()
    )


def _text(value: Any) -> str | None:
    if _is_empty(value):
        return None

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))

        return format(value, ".15g")

    return str(value).strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item)
            for item in value
        ]

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, timedelta):
        return value.total_seconds()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, float):
        if value != value:
            raise B24XlsxError(
                "NaN is not valid B24 source data"
            )

        if value in (
            float("inf"),
            float("-inf"),
        ):
            raise B24XlsxError(
                "Infinite number is not valid B24 source data"
            )

    return value


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(
        payload
    ).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        while True:
            block = stream.read(
                1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def _source_timezone(
    name: str,
):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        if name == "Europe/Moscow":
            return timezone(
                timedelta(hours=3),
                name="Europe/Moscow",
            )

        raise B24XlsxError(
            f"Unknown source timezone: {name!r}"
        ) from exc


def _normalize_id(
    value: Any,
    *,
    field: str,
    required: bool = False,
) -> str | None:
    if _is_empty(value):
        if required:
            raise B24XlsxError(
                f"{field} is required"
            )

        return None

    if isinstance(value, bool):
        raise B24XlsxError(
            f"{field} must not be boolean"
        )

    try:
        number = Decimal(
            str(value).strip()
        )
    except (InvalidOperation, ValueError):
        text = str(value).strip()

        if not text:
            if required:
                raise B24XlsxError(
                    f"{field} is required"
                )

            return None

        if not text.isdigit():
            raise B24XlsxError(
                f"{field} must be an integer-like identifier: {value!r}"
            )

        return text

    if not number.is_finite():
        raise B24XlsxError(
            f"{field} must be finite"
        )

    integral = number.to_integral_value()

    if number != integral:
        raise B24XlsxError(
            f"{field} must not contain a fractional part: {value!r}"
        )

    if integral < 0:
        raise B24XlsxError(
            f"{field} must not be negative"
        )

    return str(integral)


def _parse_datetime(
    value: Any,
    *,
    source_timezone: str,
    field: str,
) -> datetime | None:
    if _is_empty(value):
        return None

    zone = _source_timezone(
        source_timezone
    )

    parsed: datetime

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(
            value,
            time.min,
        )
    elif isinstance(value, str):
        text = value.strip()

        parsed_value: datetime | None = None

        try:
            parsed_value = datetime.fromisoformat(
                text
            )
        except ValueError:
            pass

        if parsed_value is None:
            for variant in (
                "%d.%m.%Y %H:%M:%S",
                "%d.%m.%Y %H:%M",
                "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
            ):
                try:
                    parsed_value = datetime.strptime(
                        text,
                        variant,
                    )
                    break
                except ValueError:
                    continue

        if parsed_value is None:
            raise B24XlsxError(
                f"Unable to parse datetime field {field}: {value!r}"
            )

        parsed = parsed_value
    else:
        raise B24XlsxError(
            f"Unsupported datetime value for {field}: {value!r}"
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=zone,
        )

    return parsed.astimezone(
        timezone.utc
    )


def _parse_duration_minutes(
    value: Any,
    *,
    field: str,
) -> int | None:
    if _is_empty(value):
        return None

    seconds: float

    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, time):
        seconds = (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        )
    elif (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        seconds = float(value) * 86400
    elif isinstance(value, str):
        text = value.strip()

        match = re.fullmatch(
            r"(?P<hours>\d+):(?P<minutes>\d{1,2})(?::(?P<seconds>\d{1,2}))?",
            text,
        )

        if not match:
            raise B24XlsxError(
                f"Unable to parse duration field {field}: {value!r}"
            )

        hours = int(
            match.group("hours")
        )

        minutes = int(
            match.group("minutes")
        )

        extra_seconds = int(
            match.group("seconds")
            or 0
        )

        if (
            minutes >= 60
            or extra_seconds >= 60
        ):
            raise B24XlsxError(
                f"Invalid duration field {field}: {value!r}"
            )

        seconds = (
            hours * 3600
            + minutes * 60
            + extra_seconds
        )
    else:
        raise B24XlsxError(
            f"Unsupported duration field {field}: {value!r}"
        )

    if seconds < 0:
        raise B24XlsxError(
            f"Negative duration field {field}: {value!r}"
        )

    minutes_float = seconds / 60

    if not minutes_float.is_integer():
        raise B24XlsxError(
            f"Duration field {field} has sub-minute precision: {value!r}"
        )

    return int(
        minutes_float
    )


def _parse_bool(
    value: Any,
    *,
    field: str,
) -> bool | None:
    if _is_empty(value):
        return None

    if isinstance(value, bool):
        return value

    if (
        isinstance(value, (int, float))
        and value in (0, 1)
    ):
        return bool(value)

    text = str(value).strip().casefold()

    if text in {
        "1",
        "true",
        "yes",
        "y",
        "да",
    }:
        return True

    if text in {
        "0",
        "false",
        "no",
        "n",
        "нет",
    }:
        return False

    raise B24XlsxError(
        f"Unable to parse boolean field {field}: {value!r}"
    )


def _parse_integer(
    value: Any,
    *,
    field: str,
) -> int | None:
    normalized = _normalize_id(
        value,
        field=field,
        required=False,
    )

    if normalized is None:
        return None

    return int(
        normalized
    )


def _parse_float(
    value: Any,
    *,
    field: str,
) -> float | None:
    if _is_empty(value):
        return None

    if isinstance(value, bool):
        raise B24XlsxError(
            f"{field} must not be boolean"
        )

    text = str(value).strip().replace(
        ",",
        ".",
    )

    if text.endswith("%"):
        text = text[:-1].strip()

    try:
        result = float(text)
    except ValueError as exc:
        raise B24XlsxError(
            f"Unable to parse numeric field {field}: {value!r}"
        ) from exc

    if not 0 <= result <= 100:
        raise B24XlsxError(
            f"{field} must be between 0 and 100: {value!r}"
        )

    return result


def _split_participants(
    value: Any,
) -> list[str]:
    if _is_empty(value):
        return []

    if isinstance(value, (list, tuple)):
        values = [
            _text(item)
            for item in value
        ]

        return [
            item
            for item in values
            if item
        ]

    parts = re.split(
        r"(?:\r\n|\r|\n|;|,)+",
        str(value),
    )

    return [
        item.strip()
        for item in parts
        if item.strip()
    ]


def _split_tags(
    value: Any,
) -> list[str]:
    if _is_empty(value):
        return []

    parts = re.split(
        r"(?:\r\n|\r|\n|;|,)+",
        str(value),
    )

    return [
        item.strip()
        for item in parts
        if item.strip()
    ]


def _hyperlink_target(
    cell: Any,
) -> str | None:
    hyperlink = getattr(
        cell,
        "hyperlink",
        None,
    )

    if hyperlink is None:
        return None

    target = getattr(
        hyperlink,
        "target",
        None,
    )

    if target:
        return str(target)

    location = getattr(
        hyperlink,
        "location",
        None,
    )

    return (
        str(location)
        if location
        else None
    )


def _normalize_task(
    *,
    row_number: int,
    raw_row: dict[str, Any],
    raw_hyperlinks: dict[str, str],
    present_columns: tuple[str, ...],
    source_timezone: str,
) -> B24TaskRecord:
    external_id = _normalize_id(
        raw_row.get("ID"),
        field="ID",
        required=True,
    )

    assert external_id is not None

    canonical: dict[str, Any] = {
        "external_id": external_id,
    }

    for header, field in TEXT_FIELDS.items():
        if header in present_columns:
            canonical[field] = _text(
                raw_row.get(header)
            )

    for header, field in OPAQUE_TEXT_FIELDS.items():
        if header in present_columns:
            canonical[field] = _text(
                raw_row.get(header)
            )

    for header, field in DATETIME_FIELDS.items():
        if header in present_columns:
            canonical[field] = _parse_datetime(
                raw_row.get(header),
                source_timezone=source_timezone,
                field=header,
            )

    for header, field in DURATION_FIELDS.items():
        if header in present_columns:
            canonical[field] = _parse_duration_minutes(
                raw_row.get(header),
                field=header,
            )

    for header, field in BOOLEAN_FIELDS.items():
        if header in present_columns:
            canonical[field] = _parse_bool(
                raw_row.get(header),
                field=header,
            )

    for header, field in INTEGER_FIELDS.items():
        if header in present_columns:
            canonical[field] = _parse_integer(
                raw_row.get(header),
                field=header,
            )

    for header, field in FLOAT_FIELDS.items():
        if header in present_columns:
            canonical[field] = _parse_float(
                raw_row.get(header),
                field=header,
            )

    for header, field in PARTICIPANT_LIST_FIELDS.items():
        if header in present_columns:
            canonical[field] = _split_participants(
                raw_row.get(header)
            )

    for header, field in TAG_FIELDS.items():
        if header in present_columns:
            canonical[field] = _split_tags(
                raw_row.get(header)
            )

    for header, field in REFERENCE_FIELDS.items():
        if header in present_columns:
            canonical[field] = _normalize_id(
                raw_row.get(header),
                field=header,
                required=False,
            )

    source_url = raw_hyperlinks.get(
        "ID"
    )

    if source_url:
        canonical[
            "source_url"
        ] = source_url

        task_match = TASK_URL_RE.search(
            source_url
        )

        if task_match:
            linked_task_id = task_match.group(
                "task_id"
            )

            if linked_task_id != external_id:
                raise B24XlsxError(
                    "Task hyperlink ID does not match cell ID "
                    f"at row {row_number}: "
                    f"{linked_task_id!r} != {external_id!r}"
                )

        group_match = GROUP_URL_RE.search(
            source_url
        )

        if group_match:
            canonical[
                "project_external_id"
            ] = group_match.group(
                "group_id"
            )

    parent_external_id = canonical.get(
        "parent_external_id"
    )

    parent_url = raw_hyperlinks.get(
        "ID базовой задачи"
    )

    if parent_external_id and parent_url:
        parent_match = TASK_URL_RE.search(
            parent_url
        )

        if (
            parent_match
            and parent_match.group(
                "task_id"
            )
            != parent_external_id
        ):
            raise B24XlsxError(
                "Parent hyperlink ID does not match parent cell "
                f"at row {row_number}"
            )

    canonical_json = _json_safe(
        canonical
    )

    extraction_input = {
        key: canonical_json.get(key)
        for key in EXTRACTION_INPUT_FIELDS
        if key in canonical_json
    }

    raw_payload = {
        "present_columns": list(
            present_columns
        ),
        "raw_row": _json_safe(
            raw_row
        ),
        "raw_hyperlinks": dict(
            sorted(
                raw_hyperlinks.items()
            )
        ),
    }

    return B24TaskRecord(
        row_number=row_number,
        external_id=external_id,
        present_columns=present_columns,
        raw_row=_json_safe(
            raw_row
        ),
        raw_hyperlinks=dict(
            sorted(
                raw_hyperlinks.items()
            )
        ),
        canonical=canonical,
        canonical_json=canonical_json,
        extraction_input=extraction_input,
        raw_hash=_stable_hash(
            raw_payload
        ),
        canonical_hash=_stable_hash(
            canonical_json
        ),
        extraction_input_hash=_stable_hash(
            extraction_input
        ),
    )


def parse_b24_xlsx(
    path: str | Path,
    *,
    source_timezone: str,
    sheet_name: str | None = None,
) -> B24WorkbookSnapshot:
    source_path = Path(
        path
    ).expanduser().resolve()

    if not source_path.is_file():
        raise B24XlsxError(
            f"XLSX does not exist: {source_path}"
        )

    _source_timezone(
        source_timezone
    )

    workbook = load_workbook(
        source_path,
        read_only=False,
        data_only=False,
    )

    try:
        if sheet_name is not None:
            if sheet_name not in workbook.sheetnames:
                raise B24XlsxError(
                    f"Worksheet not found: {sheet_name!r}"
                )

            worksheet = workbook[
                sheet_name
            ]
        else:
            worksheets = list(
                workbook.worksheets
            )

            if len(worksheets) != 1:
                raise B24XlsxError(
                    "Workbook contains multiple worksheets; "
                    "sheet_name must be specified explicitly"
                )

            worksheet = worksheets[0]

        header_values = [
            worksheet.cell(
                row=1,
                column=index,
            ).value
            for index in range(
                1,
                worksheet.max_column + 1,
            )
        ]

        last_header_index = 0

        for index, value in enumerate(
            header_values,
            start=1,
        ):
            if not _is_empty(value):
                last_header_index = index

        if last_header_index == 0:
            raise B24XlsxError(
                "Workbook has no header row"
            )

        headers = []

        for column in range(
            1,
            last_header_index + 1,
        ):
            value = worksheet.cell(
                row=1,
                column=column,
            ).value

            if _is_empty(value):
                raise B24XlsxError(
                    "Blank column header inside the used header range "
                    f"at column {column}"
                )

            headers.append(
                str(value).strip()
            )

        duplicates = sorted(
            {
                header
                for header in headers
                if headers.count(
                    header
                ) > 1
            }
        )

        if duplicates:
            raise B24XlsxError(
                "Duplicate XLSX headers: "
                + ", ".join(duplicates)
            )

        if "ID" not in headers:
            raise B24XlsxError(
                "Required XLSX column is missing: ID"
            )

        present_columns = tuple(
            headers
        )

        tasks: list[B24TaskRecord] = []
        skipped: list[B24SkippedRow] = []

        seen_ids: set[str] = set()

        row_count = 0
        hyperlink_count = 0
        formula_count = 0

        for row_number in range(
            2,
            worksheet.max_row + 1,
        ):
            raw_row: dict[str, Any] = {}
            raw_hyperlinks: dict[str, str] = {}

            row_has_content = False

            for column, header in enumerate(
                headers,
                start=1,
            ):
                cell = worksheet.cell(
                    row=row_number,
                    column=column,
                )

                value = cell.value

                raw_row[
                    header
                ] = value

                if not _is_empty(value):
                    row_has_content = True

                if cell.data_type == "f":
                    formula_count += 1

                hyperlink = _hyperlink_target(
                    cell
                )

                if hyperlink:
                    raw_hyperlinks[
                        header
                    ] = hyperlink

                    hyperlink_count += 1
                    row_has_content = True

            if not row_has_content:
                continue

            row_count += 1

            if _is_empty(
                raw_row.get("ID")
            ):
                skipped.append(
                    B24SkippedRow(
                        row_number=row_number,
                        reason="missing_task_id",
                        raw_row=_json_safe(
                            raw_row
                        ),
                    )
                )
                continue

            task = _normalize_task(
                row_number=row_number,
                raw_row=raw_row,
                raw_hyperlinks=raw_hyperlinks,
                present_columns=present_columns,
                source_timezone=source_timezone,
            )

            if task.external_id in seen_ids:
                raise B24XlsxError(
                    "Duplicate Task ID in one snapshot: "
                    + task.external_id
                )

            seen_ids.add(
                task.external_id
            )

            tasks.append(
                task
            )

        return B24WorkbookSnapshot(
            source_path=source_path,
            source_hash=_file_hash(
                source_path
            ),
            sheet_name=worksheet.title,
            source_timezone=source_timezone,
            present_columns=present_columns,
            row_count=row_count,
            task_count=len(tasks),
            skipped_rows=tuple(
                skipped
            ),
            hyperlink_count=hyperlink_count,
            formula_count=formula_count,
            tasks=tuple(
                tasks
            ),
        )
    finally:
        workbook.close()
