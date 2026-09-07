from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json_status(
    status_file: Path,
    log_file: Path,
    value: dict[str, Any],
) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_file.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(status_file)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
