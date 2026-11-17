"""
WorkMind Backup Engine (_backup_engine.py)

Internal Python implementation called by backup_workmind.ps1.
Do NOT call this directly in production — use the PowerShell wrapper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
import sys

# ---------------------------------------------------------------------------
# Logging — write to stdout so PowerShell does not treat log lines as errors.
# (PowerShell treats any native-command stderr output as error records,
#  which sets the overall exit code to 1 even when Python exits with 0.)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("workmind.backup")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WORKMIND_VERSION = "0.12.1"
FORMAT_VERSION = 1
TASK_NAME = "WorkMind Mail Sync"
POSTGRES_CONTAINER = "workmind-postgres"
DB_USER = "workmind"
DB_NAME = "workmind"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_git_info(repo: Path) -> tuple[str, str]:
    """Return (commit_hash, tag). Never raises."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit = ""
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=repo, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        tag = ""
    return commit, tag


def run_ps(cmd: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def task_state() -> str | None:
    """Return numeric Task Scheduler state, or None if task is absent.

    PowerShell prints the enum name (e.g. 'Running' or 'Disabled') by
    default. Explicitly convert it to an integer in the child process:
    1=Disabled, 2=Queued, 3=Ready, 4=Running.
    """
    # A name-specific lookup emits a non-terminating error for a missing
    # task. PowerShell may consequently exit with code 1 even under
    # -ErrorAction SilentlyContinue. Enumerate and filter instead, so a
    # missing task is a successful, explicit no-op, while genuine Task
    # Scheduler errors still abort the backup.
    res = run_ps(
        '$ErrorActionPreference = "Stop"; '
        'try { '
        '$tasks = @(Get-ScheduledTask -ErrorAction Stop | '
        f'Where-Object {{ $_.TaskName -eq "{TASK_NAME}" }}); '
        'if ($tasks.Count -gt 1) { '
        'throw "Multiple scheduled tasks share the configured name" '
        '}; '
        'if ($tasks.Count -eq 1) { [int]$tasks[0].State }; '
        'exit 0 '
        '} catch { '
        '[Console]::Error.WriteLine($_.Exception.Message); '
        'exit 1 '
        '}'
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Could not inspect scheduled task {TASK_NAME!r}: "
            f"{res.stderr.strip()}"
        )
    val = res.stdout.strip()
    if not val:
        return None
    if val not in {"0", "1", "2", "3", "4"}:
        raise RuntimeError(
            f"Unexpected scheduled task state for {TASK_NAME!r}: {val!r}"
        )
    return val


def wait_for_task_idle(timeout_seconds: int = 300) -> None:
    """Block while the scheduled task is queued or running."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = task_state()
        if state is None:
            logger.warning("Scheduled task '%s' not found — skipping wait.", TASK_NAME)
            return
        if state not in {"2", "4"}:  # Queued / Running
            return
        logger.info("Task '%s' is busy (state=%s). Waiting 10 s...", TASK_NAME, state)
        time.sleep(10)
    raise TimeoutError(
        f"Task '{TASK_NAME}' still running after {timeout_seconds}s"
    )


def disable_task() -> bool:
    """Disable the scheduled task. Returns True if successfully disabled."""
    state = task_state()
    if state is None:
        logger.warning("Scheduled task '%s' not found — no scheduler control.", TASK_NAME)
        return False
    if state == "1":  # already Disabled
        logger.info("Task '%s' already disabled — leaving as-is.", TASK_NAME)
        return False
    res = run_ps(f'Disable-ScheduledTask -TaskName "{TASK_NAME}" | Out-Null')
    if res.returncode != 0:
        logger.warning("Could not disable task: %s", res.stderr.strip())
        return False
    logger.info("Scheduled task '%s' disabled.", TASK_NAME)
    return True


def enable_task() -> None:
    """Re-enable the scheduled task."""
    res = run_ps(f'Enable-ScheduledTask -TaskName "{TASK_NAME}" | Out-Null')
    if res.returncode != 0:
        logger.error(
            "CRITICAL: Could not re-enable task '%s': %s — "
            "please re-enable manually!", TASK_NAME, res.stderr.strip(),
        )
    else:
        logger.info("Scheduled task '%s' re-enabled.", TASK_NAME)


# ---------------------------------------------------------------------------
# PostgreSQL backup
# ---------------------------------------------------------------------------

def backup_postgres(backup_dir: Path) -> dict[str, Any]:
    """Run pg_dump -Fc inside docker container, save to backup_dir/postgres/."""
    pg_dir = backup_dir / "postgres"
    pg_dir.mkdir(parents=True, exist_ok=True)
    dump_file = pg_dir / "workmind.dump"

    logger.info("Running pg_dump -Fc via docker exec...")
    # NOTE: Do NOT use -t (allocate TTY) — it corrupts binary stdout.
    # NOTE: Do NOT use -i (keep stdin open) — after Docker Desktop 4.90.0 upgrade,
    #       inheriting the parent stdin causes docker exec to hang indefinitely.
    #       pg_dump does not need stdin; use DEVNULL to explicitly detach it.
    cmd = [
        "docker", "exec",
        POSTGRES_CONTAINER,
        "pg_dump", "-U", DB_USER, "-Fc", DB_NAME,
    ]
    with dump_file.open("wb") as fh:
        result = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
        )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        dump_file.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {stderr}")

    dump_size = dump_file.stat().st_size
    if dump_size < 1024:
        raise RuntimeError(
            f"pg_dump produced suspiciously small dump: {dump_size} bytes — aborting."
        )

    dump_sha256 = sha256_file(dump_file)
    logger.info(
        "PostgreSQL dump: %.1f MB  sha256=%s…",
        dump_size / 1_048_576, dump_sha256[:16],
    )

    # Fetch server-side metadata (read-only query)
    pg_version = _pg_query("SELECT version();")
    db_name_actual = _pg_query("SELECT current_database();")
    alembic_rev = _pg_query(
        "SELECT version_num FROM alembic_version LIMIT 1;"
    )

    return {
        "database": db_name_actual or DB_NAME,
        "server_version": pg_version or "",
        "alembic_revision": alembic_rev or "",
        "format": "custom",
        "dump_file": "postgres/workmind.dump",
        "size": dump_size,
        "sha256": dump_sha256,
    }


def _pg_query(sql: str) -> str | None:
    """Run a read-only SQL query inside the postgres container."""
    # NOTE: No -i flag; stdin=DEVNULL prevents hanging after Docker Desktop 4.90.0.
    cmd = [
        "docker", "exec",
        POSTGRES_CONTAINER,
        "psql", "-U", DB_USER, "-d", DB_NAME,
        "-tAc", sql,
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=30
        )
        return out.decode("utf-8", errors="replace").strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CAS backup
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def backup_cas(
    backup_dir: Path,
    cas_root: Path,
    *,
    verify_hashes: bool = True,
) -> dict[str, Any]:
    """
    Copy CAS objects to backup_dir/cas/objects/aa/bb/<sha256>.
    Verifies SHA-256 of every file after copy if verify_hashes=True.
    """
    dest_root = backup_dir / "cas" / "objects"
    dest_root.mkdir(parents=True, exist_ok=True)

    if not cas_root.exists():
        raise RuntimeError(f"CAS root not found: {cas_root}")

    objects_count = 0
    total_bytes = 0
    errors: list[str] = []

    all_files = [p for p in cas_root.rglob("*") if p.is_file()]
    logger.info("CAS: found %d objects to back up...", len(all_files))

    for src in all_files:
        rel = src.relative_to(cas_root)
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Copy
        shutil.copy2(src, dst)
        size = dst.stat().st_size

        if verify_hashes:
            # The leaf name IS the sha256
            expected_sha = src.name
            if _SHA256_RE.fullmatch(expected_sha):
                actual_sha = sha256_file(dst)
                if actual_sha != expected_sha:
                    errors.append(
                        f"SHA-256 mismatch after copy: {rel} "
                        f"expected={expected_sha} got={actual_sha}"
                    )
                    continue

        objects_count += 1
        total_bytes += size

    if errors:
        raise RuntimeError(
            f"CAS backup integrity errors ({len(errors)}):\n" + "\n".join(errors[:10])
        )

    logger.info(
        "CAS backup: %d objects  %.1f MB",
        objects_count, total_bytes / 1_048_576,
    )
    return {
        "objects": objects_count,
        "bytes": total_bytes,
        "directory": "cas/objects",
        "verified_sha256": verify_hashes,
    }


# ---------------------------------------------------------------------------
# Qdrant snapshot
# ---------------------------------------------------------------------------

def backup_qdrant(
    backup_dir: Path,
    qdrant_url: str,
    collection: str,
) -> dict[str, Any]:
    """
    Create a Qdrant snapshot, download it, verify checksum.
    Falls back gracefully if snapshot API is unavailable.
    """
    import urllib.request
    import urllib.error

    qdrant_dir = backup_dir / "qdrant"
    qdrant_dir.mkdir(parents=True, exist_ok=True)

    info: dict[str, Any] = {
        "collection": collection,
        "points_count": 0,
        "snapshot_file": None,
        "snapshot_size": None,
        "snapshot_sha256": None,
        "status": "skipped",
    }

    # Get collection info (read-only)
    try:
        req = urllib.request.Request(
            f"{qdrant_url}/collections/{collection}",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        result = data.get("result", {})
        info["points_count"] = (
            result.get("points_count") or result.get("indexed_vectors_count") or 0
        )
    except Exception as e:
        logger.warning("Qdrant: could not fetch collection info: %s", e)

    logger.info(
        "Qdrant collection '%s': %d points. Creating snapshot...",
        collection, info["points_count"],
    )

    # Request snapshot creation
    try:
        req = urllib.request.Request(
            f"{qdrant_url}/collections/{collection}/snapshots",
            data=b"",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            snap_data = json.loads(resp.read())

        snap_name = snap_data.get("result", {}).get("name", "")
        if not snap_name:
            raise RuntimeError(f"Unexpected snapshot response: {snap_data}")

        logger.info("Snapshot created: %s — downloading...", snap_name)

        # Download snapshot
        snap_url = f"{qdrant_url}/collections/{collection}/snapshots/{snap_name}"
        snap_file = qdrant_dir / snap_name

        req_dl = urllib.request.Request(snap_url)
        with urllib.request.urlopen(req_dl, timeout=300) as resp_dl:
            with snap_file.open("wb") as fh:
                shutil.copyfileobj(resp_dl, fh)

        snap_size = snap_file.stat().st_size
        snap_sha256 = sha256_file(snap_file)

        info.update(
            snapshot_file=f"qdrant/{snap_name}",
            snapshot_size=snap_size,
            snapshot_sha256=snap_sha256,
            status="ok",
        )
        logger.info(
            "Qdrant snapshot saved: %.1f MB  sha256=%s…",
            snap_size / 1_048_576, snap_sha256[:16],
        )

        # IMPORTANT: Delete the snapshot from inside the Qdrant container immediately
        # after download. Without this, every backup permanently occupies ~5-6 GB
        # inside the Docker volume (workmind_qdrant_data / WSL2 VHDX), which never
        # shrinks automatically even after the file is deleted.
        try:
            import urllib.parse
            snap_encoded = urllib.parse.quote(snap_name, safe="")
            del_req = urllib.request.Request(
                f"{qdrant_url}/collections/{collection}/snapshots/{snap_encoded}",
                method="DELETE",
            )
            with urllib.request.urlopen(del_req, timeout=30) as del_resp:
                del_data = json.loads(del_resp.read())
            if del_data.get("status") == "ok":
                logger.info(
                    "Qdrant snapshot deleted from container (freed ~%.0f MB from Docker volume).",
                    snap_size / 1_048_576,
                )
            else:
                logger.warning(
                    "Qdrant snapshot DELETE returned unexpected response: %s", del_data
                )
        except Exception as del_err:
            logger.warning(
                "Could not delete Qdrant snapshot from container: %s — "
                "delete manually via: DELETE %s/collections/%s/snapshots/%s",
                del_err, qdrant_url, collection, snap_name,
            )

    except Exception as e:
        logger.warning(
            "Qdrant snapshot unavailable: %s — "
            "index can be rebuilt from PostgreSQL+CAS during restore.",
            e,
        )
        info["status"] = f"unavailable: {e}"

    return info


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def write_metadata(
    backup_dir: Path,
    git_commit: str,
    git_tag: str,
    alembic_rev: str,
) -> None:
    meta_dir = backup_dir / "metadata"
    meta_dir.mkdir(exist_ok=True)

    (meta_dir / "git.txt").write_text(
        f"Commit: {git_commit}\nTag: {git_tag}\n", encoding="utf-8"
    )
    (meta_dir / "alembic.txt").write_text(
        f"Revision: {alembic_rev}\n", encoding="utf-8"
    )

    env_info = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "workmind_version": WORKMIND_VERSION,
        "backup_tool_version": "1.0.0",
    }
    (meta_dir / "environment.json").write_text(
        json.dumps(env_info, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WorkMind backup engine — called by backup_workmind.ps1"
    )
    parser.add_argument(
        "--backup-root", required=True,
        help="Parent directory where the backup folder will be created.",
    )
    parser.add_argument(
        "--no-verify-cas", action="store_true",
        help="Skip SHA-256 verification of CAS files after copy (faster, less safe).",
    )
    parser.add_argument(
        "--skip-qdrant", action="store_true",
        help="Skip Qdrant snapshot (use if Qdrant is unavailable).",
    )
    args = parser.parse_args(argv)

    backup_root = Path(args.backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"WorkMindBackup-{timestamp}"
    backup_dir = backup_root / backup_name

    # Mark as partial until complete
    partial_marker = backup_root / f"{backup_name}.partial"
    partial_marker.touch()

    logger.info("=" * 60)
    logger.info("WorkMind Backup  %s", timestamp)
    logger.info("Target: %s", backup_dir)
    logger.info("=" * 60)

    # Locate project root (3 levels up from this file: scripts/backup/_backup_engine.py)
    project_root = Path(__file__).resolve().parent.parent.parent

    git_commit, git_tag = get_git_info(project_root)
    logger.info("Git: commit=%s  tag=%s", git_commit[:12] or "(unknown)", git_tag or "(none)")

    # Settings (read CAS root from config)
    try:
        sys.path.insert(0, str(project_root))
        from packages.common.config import get_settings  # type: ignore[import]
        settings = get_settings()
        cas_root = settings.workmind_home / "archive" / "objects"
        qdrant_url = settings.qdrant_url
        qdrant_collection = settings.qdrant_collection
    except Exception as e:
        logger.error("Cannot load WorkMind settings: %s", e)
        return 1

    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "created_at": utc_now_iso(),
        "workmind_version": WORKMIND_VERSION,
        "git_commit": git_commit,
        "git_tag": git_tag,
        "alembic_revision": "",  # filled after postgres backup
        "postgres": {},
        "cas": {},
        "qdrant": {},
    }

    backup_dir.mkdir(parents=True, exist_ok=True)
    task_was_disabled = False

    try:
        # 1. Wait for any running sync, then disable scheduler
        wait_for_task_idle()
        task_was_disabled = disable_task()

        # 2. PostgreSQL
        pg_info = backup_postgres(backup_dir)
        manifest["postgres"] = pg_info
        manifest["alembic_revision"] = pg_info.get("alembic_revision", "")

        # 3. CAS
        cas_info = backup_cas(
            backup_dir, cas_root, verify_hashes=not args.no_verify_cas
        )
        manifest["cas"] = cas_info

        # 4. Qdrant
        if args.skip_qdrant:
            manifest["qdrant"] = {
                "collection": qdrant_collection,
                "status": "skipped_by_flag",
                "rebuild_note": (
                    "Qdrant is a derived index. Rebuild with "
                    "'python -m apps.indexer reindex' after restore."
                ),
            }
        else:
            manifest["qdrant"] = backup_qdrant(backup_dir, qdrant_url, qdrant_collection)

        # 5. Metadata
        write_metadata(
            backup_dir,
            git_commit,
            git_tag,
            manifest["alembic_revision"],
        )

        # 6. Write manifest (no secrets)
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 7. Remove partial marker
        partial_marker.unlink(missing_ok=True)

        logger.info("=" * 60)
        logger.info("BACKUP COMPLETE: %s", backup_dir)
        logger.info(
            "  PostgreSQL: %.1f MB", manifest["postgres"]["size"] / 1_048_576
        )
        logger.info(
            "  CAS: %d objects  %.1f MB",
            manifest["cas"]["objects"],
            manifest["cas"]["bytes"] / 1_048_576,
        )
        logger.info(
            "  Qdrant: %d points  status=%s",
            manifest["qdrant"].get("points_count", 0),
            manifest["qdrant"].get("status", "?"),
        )
        logger.info("=" * 60)
        return 0

    except Exception as exc:
        logger.error("BACKUP FAILED: %s", exc)
        # Leave partial marker so the incomplete directory is clearly marked
        try:
            err_path = backup_dir / "backup_error.txt"
            err_path.write_text(str(exc), encoding="utf-8")
        except Exception:
            pass
        return 1

    finally:
        if task_was_disabled:
            enable_task()


if __name__ == "__main__":
    sys.exit(main())
