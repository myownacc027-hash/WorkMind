
from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.sources.bitrix24 import EXPECTED_AUDIT_HEADERS
from packages.sources.bitrix24 import parse_b24_xlsx


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Bitrix24 XLSX source audit."
    )

    parser.add_argument(
        "--xlsx",
        required=True,
    )

    parser.add_argument(
        "--source-timezone",
        required=True,
    )

    parser.add_argument(
        "--expect-audit-baseline",
        action="store_true",
    )

    args = parser.parse_args()

    snapshot = parse_b24_xlsx(
        Path(args.xlsx),
        source_timezone=args.source_timezone,
    )

    summary = snapshot.audit_summary()

    print("=== B24 XLSX DRY-RUN ===")

    keys = (
        "source_path",
        "source_hash",
        "sheet_name",
        "source_timezone",
        "physical_column_count",
        "row_count",
        "task_count",
        "skipped_row_count",
        "unique_task_id_count",
        "hyperlink_count",
        "formula_count",
        "parent_task_count",
        "distinct_parent_id_count",
        "description_count",
        "description_min",
        "description_median",
        "description_max",
        "normalized_description_count",
        "normalized_description_min",
        "normalized_description_median",
        "normalized_description_max",
        "empty_column_count",
    )

    for key in keys:
        print(
            f"{key}={summary[key]}"
        )

    print(
        "parents_present_in_snapshot="
        + json.dumps(
            summary["parents_present_in_snapshot"],
            ensure_ascii=False,
        )
    )

    print(
        "parents_absent_from_snapshot="
        + json.dumps(
            summary["parents_absent_from_snapshot"],
            ensure_ascii=False,
        )
    )

    print(
        "project_mappings="
        + json.dumps(
            summary["project_mappings"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    print(
        "empty_columns="
        + json.dumps(
            summary["empty_columns"],
            ensure_ascii=False,
        )
    )

    print(
        "skipped_rows="
        + json.dumps(
            [
                {
                    "row_number": item.row_number,
                    "reason": item.reason,
                }
                for item in snapshot.skipped_rows
            ],
            ensure_ascii=False,
        )
    )

    task_221516 = snapshot.task_by_external_id(
        "221516"
    )

    task_55899 = snapshot.task_by_external_id(
        "55899"
    )

    task_94104 = snapshot.task_by_external_id(
        "94104"
    )

    errors = []

    if args.expect_audit_baseline:
        expected = {
            "physical_column_count": 55,
            "row_count": 38,
            "task_count": 37,
            "skipped_row_count": 1,
            "unique_task_id_count": 37,
            "hyperlink_count": 85,
            "formula_count": 0,
            "parent_task_count": 24,
            "distinct_parent_id_count": 9,
            "description_count": 37,
            "description_min": 55,
            "description_median": 2506,
            "description_max": 14862,
            "normalized_description_count": 37,
            "normalized_description_min": 54,
            "normalized_description_median": 2506,
            "normalized_description_max": 14862,
            "empty_column_count": 21,
        }

        for key, value in expected.items():
            if summary.get(key) != value:
                errors.append(
                    f"{key}: expected {value!r}, "
                    f"got {summary.get(key)!r}"
                )

        if (
            tuple(snapshot.present_columns)
            != EXPECTED_AUDIT_HEADERS
        ):
            errors.append(
                "55-column header sequence differs from "
                "b24-source-v1 audit baseline"
            )

        expected_present = {
            "94104",
            "175020",
            "185038",
            "199109",
        }

        expected_absent = {
            "43033",
            "58730",
            "58771",
            "156043",
            "156051",
        }

        if (
            set(
                summary[
                    "parents_present_in_snapshot"
                ]
            )
            != expected_present
        ):
            errors.append(
                "parents_present_in_snapshot differs"
            )

        if (
            set(
                summary[
                    "parents_absent_from_snapshot"
                ]
            )
            != expected_absent
        ):
            errors.append(
                "parents_absent_from_snapshot differs"
            )

        expected_projects = {
            ("13", "001 Автоматизация/Разработка"),
            ("49", "ДСЛ. Операционные задачи"),
        }

        actual_projects = {
            (
                item["project_external_id"],
                item["project_name"],
            )
            for item in summary[
                "project_mappings"
            ]
        }

        if actual_projects != expected_projects:
            errors.append(
                "project mappings differ"
            )

        if task_221516 is None:
            errors.append(
                "Task 221516 not found"
            )
        else:
            if (
                task_221516.canonical.get(
                    "product"
                )
                != "1C: ERP"
            ):
                errors.append(
                    "Task 221516 product differs"
                )

            if (
                len(
                    str(
                        task_221516.canonical.get(
                            "description"
                        )
                        or ""
                    )
                )
                != 3854
            ):
                errors.append(
                    "Task 221516 description length differs"
                )

        if task_55899 is None:
            errors.append(
                "Task 55899 not found"
            )
        else:
            observers = task_55899.canonical.get(
                "observer_labels"
            ) or []

            if len(observers) != 14:
                errors.append(
                    "Task 55899 observer parsing differs: "
                    f"expected 14, got {len(observers)}"
                )

            if (
                task_55899.canonical.get(
                    "planned_duration_minutes"
                )
                is not None
            ):
                errors.append(
                    "Task 55899 planned duration must be null"
                )

            if (
                task_55899.canonical.get(
                    "spent_minutes"
                )
                != 5
            ):
                errors.append(
                    "Task 55899 spent duration differs"
                )

            deadline = task_55899.canonical.get(
                "deadline_at"
            )

            if (
                deadline is None
                or deadline.isoformat()
                != "2026-12-30T15:00:00+00:00"
            ):
                errors.append(
                    "Task 55899 timezone conversion differs"
                )

        if task_94104 is None:
            errors.append(
                "Task 94104 not found"
            )
        else:
            tags = set(
                task_94104.canonical.get(
                    "tags"
                )
                or []
            )

            if tags != {
                "#ДСЛ/черновик заявки в IT.",
                "ЗнИ",
            }:
                errors.append(
                    "Task 94104 tag parsing differs"
                )

        if errors:
            print("audit_baseline_match=false")

            for error in errors:
                print(
                    "AUDIT_ERROR="
                    + error
                )

            return 2

        print("audit_baseline_match=true")

    if task_221516 is not None:
        print("sample_221516_found=true")
        print(
            "sample_221516_raw_hash="
            + task_221516.raw_hash
        )
        print(
            "sample_221516_canonical_hash="
            + task_221516.canonical_hash
        )
        print(
            "sample_221516_extraction_input_hash="
            + task_221516.extraction_input_hash
        )

    print("database_writes=false")
    print("qdrant_writes=false")
    print("remote_api_calls=false")
    print("knowledge_apply=false")
    print(
        "RESULT=WORKMIND_02D14D_R1_B24_XLSX_DRY_RUN_COMPLETE"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
