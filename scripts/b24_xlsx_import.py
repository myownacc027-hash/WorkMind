
from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.database.session import SessionLocal
from packages.sources.bitrix24 import parse_b24_xlsx
from packages.sources.bitrix24_persistence import (
    import_b24_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import a Bitrix24 XLSX snapshot "
            "into the WorkMind source layer."
        )
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
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    path = Path(
        args.xlsx
    ).expanduser().resolve()

    snapshot = parse_b24_xlsx(
        path,
        source_timezone=args.source_timezone,
    )

    if not args.apply:
        result = {
            "ok": True,
            "apply": False,
            "source_hash": (
                snapshot.source_hash
            ),
            "task_count": (
                snapshot.task_count
            ),
            "skipped_row_count": (
                snapshot.skipped_row_count
            ),
            "database_writes": False,
        }

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

        return 0

    with SessionLocal() as session:
        try:
            result = import_b24_snapshot(
                session,
                snapshot,
                transport="xlsx",
                source_name=path.name,
            )

            session.commit()
        except Exception:
            session.rollback()
            raise

    result["apply"] = True

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
