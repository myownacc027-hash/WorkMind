from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from apps.attachment_processor.common import write_json_status
from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.database.models import ExternalDocument
from packages.database.session import SessionLocal
from packages.processing.documents import discover_files, import_one_file, sha256_file
from sqlalchemy import select

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Import standalone work documents into WorkMind.",
)


def _preview(path: Path, supported: set[str]) -> dict[str, Any]:
    return {
        "filename": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "extension": path.suffix.casefold(),
        "supported": path.suffix.casefold() in supported,
    }


@app.callback()
def run(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Copy originals to the archive, extract, chunk, and index them.",
    ),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    max_files: int = typer.Option(0, min=0, help="0 means all discovered files."),
) -> None:
    settings = get_settings()
    files = discover_files(settings.document_import_dir, recursive=recursive)
    if max_files:
        files = files[:max_files]

    if not apply:
        with SessionLocal() as session:
            previews: list[dict[str, Any]] = []
            for path in files:
                value = _preview(path, settings.document_supported_extensions)
                try:
                    content_hash = sha256_file(path)
                    existing = session.scalar(
                        select(ExternalDocument.id).where(
                            ExternalDocument.content_hash == content_hash
                        )
                    )
                    value["content_hash"] = content_hash
                    value["duplicate"] = existing is not None
                    value["existing_document_id"] = (
                        str(existing) if existing else None
                    )
                except OSError as exc:
                    value["error"] = str(exc)
                previews.append(value)
        typer.echo(json.dumps({
            "apply": False,
            "import_dir": str(settings.document_import_dir),
            "discovered": len(files),
            "files": previews,
            "note": "Dry run only. Repeat with --apply to import.",
        }, ensure_ascii=False, indent=2))
        return

    with single_instance_lock(settings.document_import_lock_file) as acquired:
        if not acquired:
            skipped = {
                "status": "skipped_locked",
                "timestamp": datetime.now(UTC).isoformat(),
                "pid": os.getpid(),
                "import_dir": str(settings.document_import_dir),
                "discovered": len(files),
                "note": "Another document import is already running.",
            }
            write_json_status(
                settings.document_import_status_file,
                settings.document_import_log_file,
                skipped,
            )
            typer.echo(json.dumps(skipped, ensure_ascii=False, indent=2))
            raise typer.Exit(3)

        started = datetime.now(UTC)
        started_mono = time.monotonic()
        running_status = {
            "status": "running",
            "started_at": started.isoformat(),
            "pid": os.getpid(),
            "import_dir": str(settings.document_import_dir),
            "discovered": len(files),
        }
        write_json_status(
            settings.document_import_status_file,
            settings.document_import_log_file,
            running_status,
        )

        results: list[dict[str, Any]] = []
        counters = {
            "indexed": 0,
            "duplicate": 0,
            "unsupported": 0,
            "empty_no_text": 0,
            "protected": 0,
            "too_large": 0,
            "missing": 0,
            "corrupt": 0,
            "error": 0,
        }
        try:
            with SessionLocal() as session:
                for path in files:
                    try:
                        result = import_one_file(
                            session,
                            source=path,
                            settings=settings,
                        )
                    except Exception as exc:
                        session.rollback()
                        result = {
                            "status": "error",
                            "filename": path.name,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    results.append(result)
                    status = str(result.get("status") or "error")
                    counters[status] = counters.get(status, 0) + 1

            final_status = (
                "completed_with_errors"
                if counters.get("error", 0) > 0
                else "completed"
            )
            status_value = {
                "status": final_status,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started_mono, 3),
                "pid": os.getpid(),
                "import_dir": str(settings.document_import_dir),
                "discovered": len(files),
                "counters": counters,
                "results": results,
            }
        except Exception as exc:
            status_value = {
                "status": "failed",
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started_mono, 3),
                "pid": os.getpid(),
                "import_dir": str(settings.document_import_dir),
                "discovered": len(files),
                "counters": counters,
                "results": results,
                "error": f"{type(exc).__name__}: {exc}",
            }
            write_json_status(
                settings.document_import_status_file,
                settings.document_import_log_file,
                status_value,
            )
            typer.echo(json.dumps(status_value, ensure_ascii=False, indent=2))
            raise

        write_json_status(
            settings.document_import_status_file,
            settings.document_import_log_file,
            status_value,
        )
        typer.echo(json.dumps(status_value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
