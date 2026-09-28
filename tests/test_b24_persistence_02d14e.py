
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
import pytest
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.database.models import (
    Base,
    B24ImportSnapshot,
    B24Task,
    B24TaskObservation,
)
from packages.sources.bitrix24 import (
    parse_b24_xlsx,
    stable_payload_hash,
)
from packages.sources.bitrix24_persistence import (
    import_b24_snapshot,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(
    _type: JSONB,
    _compiler: Any,
    **_kwargs: Any,
) -> str:
    return "JSON"


TABLES = [
    B24ImportSnapshot.__table__,
    B24Task.__table__,
    B24TaskObservation.__table__,
]


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    Base.metadata.create_all(
        engine,
        tables=TABLES,
    )

    result = sessionmaker(
        engine,
        expire_on_commit=False,
    )

    try:
        yield result
    finally:
        engine.dispose()


def _book(
    path: Path,
    *,
    headers: list[str],
    rows: list[list[object]],
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

    workbook.save(
        path
    )

    workbook.close()

    return path


def _count(
    session: Session,
    model,
) -> int:
    return int(
        session.scalar(
            select(
                func.count()
            ).select_from(
                model
            )
        )
        or 0
    )


def test_first_import_creates_snapshot_tasks_and_observations(
    tmp_path: Path,
    factory,
) -> None:
    path = _book(
        tmp_path / "one.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
            "Статус",
        ],
        rows=[
            [
                1,
                "Task one",
                "Description one",
                "Waiting",
            ],
            [
                2,
                "Task two",
                "Description two",
                "Running",
            ],
        ],
    )

    snapshot = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    )

    with factory() as session:
        result = import_b24_snapshot(
            session,
            snapshot,
        )

        session.commit()

        assert result[
            "already_imported"
        ] is False

        assert result[
            "created_tasks"
        ] == 2

        assert result[
            "observations_created"
        ] == 2

        assert _count(
            session,
            B24ImportSnapshot,
        ) == 1

        assert _count(
            session,
            B24Task,
        ) == 2

        assert _count(
            session,
            B24TaskObservation,
        ) == 2


def test_same_binary_snapshot_is_idempotent(
    tmp_path: Path,
    factory,
) -> None:
    path = _book(
        tmp_path / "same.xlsx",
        headers=[
            "ID",
            "Название",
            "Описание",
        ],
        rows=[
            [
                1,
                "Task",
                "Description",
            ],
        ],
    )

    snapshot = parse_b24_xlsx(
        path,
        source_timezone="Europe/Moscow",
    )

    with factory() as session:
        first = import_b24_snapshot(
            session,
            snapshot,
        )
        session.commit()

        second = import_b24_snapshot(
            session,
            snapshot,
        )
        session.commit()

        assert first[
            "already_imported"
        ] is False

        assert second[
            "already_imported"
        ] is True

        assert second[
            "observations_created"
        ] == 0

        assert _count(
            session,
            B24ImportSnapshot,
        ) == 1

        assert _count(
            session,
            B24Task,
        ) == 1

        assert _count(
            session,
            B24TaskObservation,
        ) == 1


def test_partial_snapshot_preserves_absent_fields(
    tmp_path: Path,
    factory,
) -> None:
    first_path = _book(
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
                "Persistent description",
                "Waiting",
            ],
        ],
    )

    second_path = _book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Статус",
        ],
        rows=[
            [
                1,
                "Running",
            ],
        ],
    )

    first = parse_b24_xlsx(
        first_path,
        source_timezone="Europe/Moscow",
    )

    second = parse_b24_xlsx(
        second_path,
        source_timezone="Europe/Moscow",
    )

    with factory() as session:
        import_b24_snapshot(
            session,
            first,
        )
        session.commit()

        task = session.scalar(
            select(B24Task).where(
                B24Task.external_id == "1"
            )
        )

        assert task is not None

        original_extraction_hash = (
            task.extraction_input_hash
        )

        result = import_b24_snapshot(
            session,
            second,
        )
        session.commit()

        task = session.scalar(
            select(B24Task).where(
                B24Task.external_id == "1"
            )
        )

        assert task is not None
        assert task.description == (
            "Persistent description"
        )

        assert task.status == "Running"

        assert (
            task.canonical_json[
                "description"
            ]
            == "Persistent description"
        )

        assert (
            task.extraction_input_hash
            == original_extraction_hash
        )

        assert result[
            "canonical_changed"
        ] == 1

        assert result[
            "extraction_input_changed"
        ] == 0

        assert (
            stable_payload_hash(
                task.canonical_json
            )
            == task.canonical_hash
        )

        assert (
            stable_payload_hash(
                task.extraction_input_json
            )
            == task.extraction_input_hash
        )


def test_present_empty_cell_clears_previous_value(
    tmp_path: Path,
    factory,
) -> None:
    first_path = _book(
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
                "Description",
            ],
        ],
    )

    second_path = _book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Описание",
        ],
        rows=[
            [
                1,
                None,
            ],
        ],
    )

    first = parse_b24_xlsx(
        first_path,
        source_timezone="Europe/Moscow",
    )

    second = parse_b24_xlsx(
        second_path,
        source_timezone="Europe/Moscow",
    )

    with factory() as session:
        import_b24_snapshot(
            session,
            first,
        )
        session.commit()

        import_b24_snapshot(
            session,
            second,
        )
        session.commit()

        task = session.scalar(
            select(B24Task).where(
                B24Task.external_id == "1"
            )
        )

        assert task is not None
        assert task.description is None
        assert (
            task.canonical_json[
                "description"
            ]
            is None
        )


def test_absent_task_in_partial_snapshot_is_not_deleted(
    tmp_path: Path,
    factory,
) -> None:
    first_path = _book(
        tmp_path / "first.xlsx",
        headers=[
            "ID",
            "Название",
        ],
        rows=[
            [1, "One"],
            [2, "Two"],
        ],
    )

    second_path = _book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Статус",
        ],
        rows=[
            [1, "Running"],
        ],
    )

    first = parse_b24_xlsx(
        first_path,
        source_timezone="Europe/Moscow",
    )

    second = parse_b24_xlsx(
        second_path,
        source_timezone="Europe/Moscow",
    )

    with factory() as session:
        import_b24_snapshot(
            session,
            first,
        )
        session.commit()

        import_b24_snapshot(
            session,
            second,
        )
        session.commit()

        assert _count(
            session,
            B24Task,
        ) == 2

        task_two = session.scalar(
            select(B24Task).where(
                B24Task.external_id == "2"
            )
        )

        assert task_two is not None
        assert task_two.title == "Two"


def test_observation_keeps_snapshot_payload_not_merged_state(
    tmp_path: Path,
    factory,
) -> None:
    first_path = _book(
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
                "Description",
            ],
        ],
    )

    second_path = _book(
        tmp_path / "second.xlsx",
        headers=[
            "ID",
            "Статус",
        ],
        rows=[
            [
                1,
                "Running",
            ],
        ],
    )

    first = parse_b24_xlsx(
        first_path,
        source_timezone="Europe/Moscow",
    )

    second = parse_b24_xlsx(
        second_path,
        source_timezone="Europe/Moscow",
    )

    with factory() as session:
        import_b24_snapshot(
            session,
            first,
        )
        session.commit()

        import_b24_snapshot(
            session,
            second,
        )
        session.commit()

        observations = list(
            session.scalars(
                select(
                    B24TaskObservation
                ).order_by(
                    B24TaskObservation.observed_at,
                    B24TaskObservation.id,
                )
            )
        )

        assert len(
            observations
        ) == 2

        latest = observations[-1]

        assert "status" in (
            latest.canonical_json
        )

        assert "description" not in (
            latest.canonical_json
        )

        task = session.scalar(
            select(B24Task).where(
                B24Task.external_id == "1"
            )
        )

        assert task is not None

        assert (
            task.canonical_json[
                "description"
            ]
            == "Description"
        )
