from __future__ import annotations

import json
import sys

from apps.mcp_server.services import system_status


def main() -> None:
    try:
        result = {
            "ok": True,
            "status": system_status(),
        }
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
