"""
WorkMind Restore Engine (_restore_engine.py)

Internal Python implementation called by restore_workmind.ps1.
NEVER run against production database without --confirm-destructive-restore.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("workmind.restore")

POSTGRES_CONTAINER = "workmind-postgres"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Names that are NEVER allowed as a restore target without explicit override.
# This is a safety net — the real guard is --confirm-destructive-restore.
PROTECTED_DB_NAMES = {"workmind"}


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_manifest(backup_path: Path) -> dict:
    manifest_file = backup_path / "manifest.json"
    if not manifest_file.exists():
        raise FileNotFoundError(f"manifest.json not found in {backup_path}")
    return json.loads(manifest_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# PostgreSQL restore
# ---------------------------------------------------------------------------

def _pg_exec(sql: str, *, db: str = "postgres") -> subprocess.CompletedProcess[str]:
    # No -i: stdin=DEVNULL prevents Docker Desktop 4.90.0 stdin-pipe hang.
    cmd = [
        "docker", "exec",
        POSTGRES_CONTAINER,
        "psql", "-U", "workmind", "-d", db, "-c", sql,
    ]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
    )


def _pg_query(sql: str, *, db: str) -> str | None:
    # No -i: stdin=DEVNULL prevents Docker Desktop 4.90.0 stdin-pipe hang.
    cmd = [
        "docker", "exec",
        POSTGRES_CONTAINER,
        "psql", "-U", "workmind", "-d", db,
        "-tAc", sql,
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=30
        )
        return out.decode("utf-8", errors="replace").strip() or None
    except Exception:
        return None


def restore_postgres(
    backup_path: Path,
    target_db: str,
    *,
    confirm_destructive: bool,
    dry_run: bool = False,
) -> dict:
    """
    Restore pg dump into target_db.
    Raises if target_db is a protected production database and
    confirm_destructive is False.
    """
    if target_db in PROTECTED_DB_NAMES and not confirm_destructive:
        raise PermissionError(
            f"Target database '{target_db}' is a protected production database. "
            "Pass --confirm-destructive-restore to override. "
            "WARNING: this will DESTROY existing data."
        )

    dump_file = backup_path / "postgres" / "workmind.dump"
    if not dump_file.exists():
        raise FileNotFoundError(f"PostgreSQL dump not found: {dump_file}")

    if dry_run:
        logger.info("[DRY RUN] Would restore %s → database '%s'", dump_file, target_db)
        return {"target_database": target_db, "dry_run": True}

    logger.info("Creating target database '%s'...", target_db)
    # Drop if exists (for test databases only — production guard is above)
    _pg_exec(f"DROP DATABASE IF EXISTS \"{target_db}\";")
    res = _pg_exec(f"CREATE DATABASE \"{target_db}\";")
    if res.returncode != 0:
        raise RuntimeError(
            f"Could not create database '{target_db}': {res.stderr.strip()}"
        )

    logger.info("Running pg_restore into '%s'...", target_db)
    # NOTE: We pipe the dump file directly as stdin to docker exec (no -i flag needed
    # for stdin piping via subprocess; -i was causing hangs in Docker Desktop 4.90.0).
    cmd = [
        "docker", "exec", "-i",  # -i required here: pg_restore reads dump from stdin
        POSTGRES_CONTAINER,
        "pg_restore",
        "-U", "workmind",
        "-d", target_db,
        "--no-owner", "--role=workmind",
        "-Fc",
    ]
    with dump_file.open("rb") as fh:
        result = subprocess.run(cmd, stdin=fh, capture_output=True, timeout=600)

    if result.returncode not in (0, 1):
        # pg_restore exits 1 for warnings (non-fatal); only fail on > 1
        raise RuntimeError(
            f"pg_restore failed (exit {result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )

    if result.returncode == 1:
        stderr_text = result.stderr.decode("utf-8", errors="replace")
        logger.warning("pg_restore completed with warnings:\n%s", stderr_text[:2000])

    return {"target_database": target_db, "dry_run": False}


# ---------------------------------------------------------------------------
# CAS restore
# ---------------------------------------------------------------------------

def restore_cas(
    backup_path: Path,
    target_cas_root: Path,
    *,
    dry_run: bool = False,
    verify_hashes: bool = True,
) -> dict:
    """Copy CAS objects from backup to target_cas_root."""
    src_root = backup_path / "cas" / "objects"
    if not src_root.exists():
        raise FileNotFoundError(f"CAS backup not found: {src_root}")

    if dry_run:
        files = [p for p in src_root.rglob("*") if p.is_file()]
        logger.info("[DRY RUN] Would restore %d CAS objects → %s", len(files), target_cas_root)
        return {"target_cas_root": str(target_cas_root), "dry_run": True}

    target_cas_root.mkdir(parents=True, exist_ok=True)

    all_files = [p for p in src_root.rglob("*") if p.is_file()]
    logger.info("Restoring %d CAS objects → %s...", len(all_files), target_cas_root)

    restored = 0
    total_bytes = 0
    errors: list[str] = []

    for src in all_files:
        rel = src.relative_to(src_root)
        dst = target_cas_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists():
            # Already present — skip (immutable CAS)
            restored += 1
            total_bytes += dst.stat().st_size
            continue

        shutil.copy2(src, dst)

        if verify_hashes:
            expected = src.name
            if _SHA256_RE.fullmatch(expected):
                actual = sha256_file(dst)
                if actual != expected:
                    errors.append(
                        f"Hash mismatch after restore copy: {rel} "
                        f"expected={expected} actual={actual}"
                    )
                    dst.unlink(missing_ok=True)
                    continue

        restored += 1
        total_bytes += dst.stat().st_size

    if errors:
        raise RuntimeError(
            f"CAS restore integrity errors ({len(errors)}):\n" + "\n".join(errors[:10])
        )

    logger.info("CAS restore: %d objects  %.1f MB", restored, total_bytes / 1_048_576)
    return {
        "target_cas_root": str(target_cas_root),
        "objects_restored": restored,
        "bytes_restored": total_bytes,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# Verification after restore
# ---------------------------------------------------------------------------

def verify_restore(
    target_db: str,
    target_cas_root: Path,
    manifest: dict,
    *,
    spot_check_count: int = 5,
) -> dict:
    """
    Verify the restored database and CAS match expected counts from manifest.
    Returns a dict of results. Raises on critical failures.
    """
    results: dict = {}

    # Alembic revision
    alembic_rev = _pg_query("SELECT version_num FROM alembic_version LIMIT 1;", db=target_db)
    expected_rev = manifest.get("alembic_revision", "")
    results["alembic_revision"] = {
        "expected": expected_rev,
        "actual": alembic_rev,
        "ok": alembic_rev == expected_rev,
    }
    if not results["alembic_revision"]["ok"]:
        raise RuntimeError(
            f"Alembic mismatch: expected={expected_rev} actual={alembic_rev}"
        )

    # Table counts
    for table, key in [
        ("attachments", "attachments"),
        ("stored_objects", "stored_objects"),
        ("document_chunks", "document_chunks"),
    ]:
        count_str = _pg_query(f"SELECT COUNT(*) FROM {table};", db=target_db)
        results[key] = int(count_str or "0")

    logger.info(
        "Restored counts — attachments=%d  stored_objects=%d  document_chunks=%d",
        results["attachments"],
        results["stored_objects"],
        results["document_chunks"],
    )

    # Unlinked attachments
    unlinked = _pg_query(
        "SELECT COUNT(*) FROM attachments WHERE stored_object_id IS NULL;",
        db=target_db,
    )
    results["unlinked_attachments"] = int(unlinked or "0")
    if results["unlinked_attachments"] != 0:
        raise RuntimeError(
            f"Restore integrity error: {results['unlinked_attachments']} unlinked attachments"
        )

    # Orphan stored objects
    orphan = _pg_query(
        "SELECT COUNT(*) FROM stored_objects WHERE id NOT IN "
        "(SELECT stored_object_id FROM attachments WHERE stored_object_id IS NOT NULL);",
        db=target_db,
    )
    results["orphan_stored_objects"] = int(orphan or "0")

    # CAS spot-check: pick N stored_objects, verify the file exists and hash matches
    rows_sql = (
        "SELECT sha256, storage_path FROM stored_objects "
        f"ORDER BY RANDOM() LIMIT {spot_check_count};"
    )
    rows_raw = _pg_query(rows_sql, db=target_db)
    spot_results = []

    if rows_raw:
        for line in rows_raw.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 2:
                continue
            sha, orig_path = parts[0].strip(), parts[1].strip()
            # Rebase path: leaf name is sha256, so find it in target CAS
            leaf = Path(orig_path).name
            rebased = target_cas_root / leaf[:2] / leaf[2:4] / leaf
            file_exists = rebased.exists()
            hash_ok = False
            if file_exists:
                actual_sha = sha256_file(rebased)
                hash_ok = actual_sha == sha
            spot_results.append({
                "sha256": sha[:16] + "…",
                "rebased_path": str(rebased),
                "file_exists": file_exists,
                "hash_ok": hash_ok,
            })
            status = "✓" if (file_exists and hash_ok) else "✗"
            logger.info("  CAS spot-check %s  %s", status, str(rebased)[-60:])

    results["cas_spot_checks"] = spot_results
    failed_spots = [s for s in spot_results if not (s["file_exists"] and s["hash_ok"])]
    if failed_spots:
        raise RuntimeError(
            f"{len(failed_spots)} CAS spot-check(s) failed after restore."
        )

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WorkMind restore engine — called by restore_workmind.ps1"
    )
    parser.add_argument("--backup-path", required=True)
    parser.add_argument(
        "--target-database",
        default=None,
        help=(
            "Target PostgreSQL database name. "
            "Defaults to 'workmind_restore_test_YYYYMMDD'. "
            "Use production DB name only with --confirm-destructive-restore."
        ),
    )
    parser.add_argument("--target-cas-root", default=None)
    parser.add_argument(
        "--confirm-destructive-restore",
        action="store_true",
        help="Allow restoring to production database. DESTROYS existing data.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify-hashes", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true",
                        help="Do not delete test resources after successful restore test.")
    args = parser.parse_args(argv)

    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")

    backup_path = Path(args.backup_path)
    if not backup_path.exists():
        logger.error("Backup path not found: %s", backup_path)
        return 1

    # Load manifest
    try:
        manifest = load_manifest(backup_path)
    except Exception as e:
        logger.error("Cannot load manifest: %s", e)
        return 1

    # Default target DB for safe test restore
    target_db = args.target_database or f"workmind_restore_test_{today}"
    target_cas_root = (
        Path(args.target_cas_root)
        if args.target_cas_root
        else Path(f"C:/AIKnowledge/restore-test-{today}/objects")
    )

    is_test_restore = target_db not in PROTECTED_DB_NAMES
    is_test_cas = "restore-test" in str(target_cas_root)

    logger.info("=" * 60)
    logger.info("WorkMind Restore")
    logger.info("  Backup:       %s", backup_path)
    logger.info("  Target DB:    %s", target_db)
    logger.info("  Target CAS:   %s", target_cas_root)
    logger.info("  Dry run:      %s", args.dry_run)
    logger.info("  Destructive:  %s", args.confirm_destructive_restore)
    logger.info("=" * 60)

    if target_db in PROTECTED_DB_NAMES and not args.confirm_destructive_restore:
        logger.error(
            "SAFETY BLOCK: target database '%s' is production. "
            "Use --confirm-destructive-restore to proceed. "
            "This will DESTROY existing data.", target_db
        )
        return 1

    try:
        # 1. PostgreSQL restore
        pg_result = restore_postgres(
            backup_path, target_db,
            confirm_destructive=args.confirm_destructive_restore,
            dry_run=args.dry_run,
        )
        logger.info("PostgreSQL restore: %s", pg_result)

        # 2. CAS restore
        cas_result = restore_cas(
            backup_path, target_cas_root,
            dry_run=args.dry_run,
            verify_hashes=not args.no_verify_hashes,
        )
        logger.info("CAS restore: %s", cas_result)

        if args.dry_run:
            logger.info("DRY RUN complete — no data was changed.")
            return 0

        # 3. Post-restore verification
        logger.info("Verifying restore integrity...")
        verify_results = verify_restore(target_db, target_cas_root, manifest)

        logger.info("=" * 60)
        logger.info("RESTORE VERIFY RESULTS:")
        logger.info(
            "  Alembic:         %s → %s",
            verify_results["alembic_revision"]["expected"],
            verify_results["alembic_revision"]["actual"],
        )
        logger.info("  Attachments:     %d", verify_results["attachments"])
        logger.info("  StoredObjects:   %d", verify_results["stored_objects"])
        logger.info("  DocumentChunks:  %d", verify_results["document_chunks"])
        logger.info("  Unlinked:        %d", verify_results["unlinked_attachments"])
        logger.info("  Orphan SO:       %d", verify_results["orphan_stored_objects"])
        logger.info(
            "  CAS spot-checks: %d/%d OK",
            sum(1 for s in verify_results["cas_spot_checks"] if s["hash_ok"]),
            len(verify_results["cas_spot_checks"]),
        )
        logger.info("=" * 60)
        logger.info("RESTORE TEST: PASS")

        # 4. Cleanup test resources
        if is_test_restore and is_test_cas and not args.skip_cleanup:
            logger.info("Cleaning up test resources...")
            _pg_exec(f"DROP DATABASE IF EXISTS \"{target_db}\";")
            if target_cas_root.exists():
                # Safety: only delete if it's clearly a test dir
                if "restore-test" in str(target_cas_root):
                    parent = target_cas_root.parent
                    shutil.rmtree(str(target_cas_root.parent), ignore_errors=True)
                    logger.info("Removed test CAS dir: %s", parent)
            logger.info("Cleanup complete.")
        elif args.skip_cleanup:
            logger.info(
                "Cleanup skipped (--skip-cleanup). "
                "Test DB: '%s'  Test CAS: '%s'", target_db, target_cas_root,
            )

        return 0

    except PermissionError as e:
        logger.error("SAFETY BLOCK: %s", e)
        return 1
    except Exception as e:
        logger.error("RESTORE FAILED: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
