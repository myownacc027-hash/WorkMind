from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from packages.common.config import get_settings
from packages.processing.embeddings import embedding_service_status

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _wait_until_ready(timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}

    while time.monotonic() < deadline:
        last = embedding_service_status(timeout_seconds=2.0)
        if last.get("status") == "ready":
            return last
        if last.get("status") == "error":
            return last
        time.sleep(1.0)

    return {
        "reachable": False,
        "status": "timeout",
        "error": (
            "Embedding service did not become ready within "
            f"{timeout_seconds:.0f} seconds."
        ),
        "last_status": last,
    }


def start_main() -> None:
    os.chdir(PROJECT_ROOT)
    settings = get_settings()

    current = embedding_service_status(timeout_seconds=1.0)
    if current.get("reachable") or current.get("status") in {
        "ready",
        "loading",
        "starting",
    }:
        _print(
            {
                "ok": current.get("status") == "ready",
                "already_running": True,
                "service": current,
            }
        )
        raise SystemExit(0 if current.get("status") == "ready" else 2)

    log_path = settings.embedding_service_log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        start_new_session = True

    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "apps.embedding_server.server",
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=creationflags,
            start_new_session=start_new_session,
            close_fds=True,
        )

    result = _wait_until_ready(
        settings.embedding_service_timeout_seconds
    )
    ok = result.get("status") == "ready"

    _print(
        {
            "ok": ok,
            "started_pid": process.pid,
            "log_file": str(log_path),
            "service": result,
        }
    )
    raise SystemExit(0 if ok else 1)


def status_main() -> None:
    os.chdir(PROJECT_ROOT)
    status = embedding_service_status(timeout_seconds=2.0)
    _print(
        {
            "ok": status.get("status") == "ready",
            "service": status,
        }
    )
    raise SystemExit(0 if status.get("status") == "ready" else 1)


def stop_main() -> None:
    os.chdir(PROJECT_ROOT)
    settings = get_settings()
    status = embedding_service_status(timeout_seconds=2.0)

    pid = status.get("pid")
    if not isinstance(pid, int):
        try:
            pid = int(
                settings.embedding_service_pid_file.read_text(
                    encoding="utf-8"
                ).strip()
            )
        except (OSError, ValueError):
            _print(
                {
                    "ok": True,
                    "stopped": False,
                    "message": "Embedding service is not running.",
                }
            )
            return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        _print(
            {
                "ok": False,
                "stopped": False,
                "pid": pid,
                "error": str(exc),
            }
        )
        raise SystemExit(1) from exc

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        current = embedding_service_status(timeout_seconds=1.0)
        if not current.get("reachable"):
            settings.embedding_service_pid_file.unlink(
                missing_ok=True
            )
            _print(
                {
                    "ok": True,
                    "stopped": True,
                    "pid": pid,
                }
            )
            return
        time.sleep(0.5)

    _print(
        {
            "ok": False,
            "stopped": False,
            "pid": pid,
            "error": "Service did not stop within 15 seconds.",
        }
    )
    raise SystemExit(1)
