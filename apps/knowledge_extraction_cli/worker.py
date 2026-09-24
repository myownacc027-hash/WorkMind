from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import typer

from packages.common.config import get_settings
from packages.database.models import KnowledgeExtractionJob
from packages.database.session import SessionLocal
from packages.knowledge.extraction import (
    apply_validated_extraction_job,
    build_extraction_packet,
    claim_extraction_jobs,
    validate_extraction_job,
)
from packages.knowledge.deepseek_provider import extract_with_deepseek
from packages.knowledge.openai_provider import extract_with_openai

app = typer.Typer(add_completion=False, invoke_without_command=True)


def _write_status(value: dict[str, Any]) -> None:
    settings = get_settings()
    settings.knowledge_extraction_status_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    settings.knowledge_extraction_log_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    settings.knowledge_extraction_status_file.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with settings.knowledge_extraction_log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _fail_job(job_id, error: Exception) -> None:
    with SessionLocal() as session:
        job = session.get(KnowledgeExtractionJob, job_id)
        if job is None:
            return
        settings = get_settings()
        exhausted = job.attempts >= settings.knowledge_extraction_max_attempts
        job.status = "error" if exhausted else "pending"
        job.last_error = f"{type(error).__name__}: {error}"[:4000]
        if exhausted:
            job.completed_at = datetime.now(UTC)
        job.lease_token = None
        job.leased_at = None
        job.lease_expires_at = None
        session.commit()


@app.callback()
def run(
    max_jobs: int = typer.Option(3, min=1, max=50),
    auto_apply: bool | None = typer.Option(None, "--auto-apply/--no-auto-apply"),
) -> None:
    settings = get_settings()
    provider = settings.knowledge_extraction_provider.strip().lower()
    if provider not in {"openai", "deepseek"}:
        result = {
            "status": "skipped",
            "provider": provider,
            "reason": (
                "Background worker supports provider=openai and "
                "provider=deepseek. provider=agent is processed through "
                "WorkMind MCP tools."
            ),
        }
        _write_status(result)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    apply_flag = (
        settings.knowledge_extraction_auto_apply
        if auto_apply is None
        else auto_apply
    )
    started = datetime.now(UTC)
    started_mono = time.monotonic()
    counters = {
        "claimed": 0,
        "validated": 0,
        "applied": 0,
        "failed": 0,
    }
    results: list[dict[str, Any]] = []

    jobs = claim_extraction_jobs(limit=max_jobs)
    counters["claimed"] = len(jobs)
    for claimed in jobs:
        try:
            packet = build_extraction_packet(claimed.id)
            extractor = (
                extract_with_deepseek
                if provider == "deepseek"
                else extract_with_openai
            )
            bundle_json, provider_response = extractor(
                packet,
                settings=settings,
            )
            lease_token = packet["job"]["lease_token"]
            validation = validate_extraction_job(
                claimed.id,
                lease_token,
                bundle_json,
            )
            counters["validated"] += int(
                validation.get("status") == "validated"
            )
            item: dict[str, Any] = {
                "job_id": str(claimed.id),
                "validation": validation,
                "provider_response_id": provider_response.get("id"),
            }
            if apply_flag and validation.get("status") == "validated":
                apply_result = apply_validated_extraction_job(claimed.id)
                item["apply"] = apply_result
                counters["applied"] += int(
                    apply_result.get("status") == "completed"
                )
            results.append(item)
        except Exception as exc:
            counters["failed"] += 1
            _fail_job(claimed.id, exc)
            results.append(
                {
                    "job_id": str(claimed.id),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    result = {
        "status": "completed_with_errors" if counters["failed"] else "completed",
        "provider": provider,
        "model": (
            settings.deepseek_model
            if provider == "deepseek"
            else settings.knowledge_extraction_model
        ),
        "auto_apply": apply_flag,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(time.monotonic() - started_mono, 3),
        "counters": counters,
        "results": results,
    }
    _write_status(result)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if counters["failed"]:
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
