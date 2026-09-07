from __future__ import annotations

import json

from packages.common.config import get_settings


def main() -> None:
    settings = get_settings()
    path = settings.history_status_file

    if not path.exists():
        print(
            json.dumps(
                {
                    "status": "never_run",
                    "status_file": str(path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "status": "invalid_status_file",
                    "status_file": str(path),
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1) from exc

    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
