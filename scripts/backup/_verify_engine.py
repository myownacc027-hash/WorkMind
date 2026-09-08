"""
WorkMind Verify Engine (_verify_engine.py)

Read-only verification of a completed backup.
Exits 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("workmind.verify")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _fail(msg: str) -> None:
    logger.error("FAIL  %s", msg)


def _ok(msg: str) -> None:
    logger.info("OK    %s", msg)


def verify_backup(backup_path: Path, *, verify_all_hashes: bool = False) -> bool:
    """Return True if backup passes all checks."""
    passed = True

    # ------------------------------------------------------------------ #
    # 1. Manifest                                                          #
    # ------------------------------------------------------------------ #
    manifest_file = backup_path / "manifest.json"
    if not manifest_file.exists():
        _fail("manifest.json missing")
        return False

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(f"manifest.json parse error: {e}")
        return False

    _ok(f"manifest.json present  format_version={manifest.get('format_version')}")
    logger.info(
        "     created_at=%s  workmind=%s  alembic=%s",
        manifest.get("created_at"),
        manifest.get("workmind_version"),
        manifest.get("alembic_revision"),
    )

    # ------------------------------------------------------------------ #
    # 2. PostgreSQL dump                                                   #
    # ------------------------------------------------------------------ #
    pg_info = manifest.get("postgres", {})
    dump_rel = pg_info.get("dump_file", "postgres/workmind.dump")
    dump_file = backup_path / dump_rel

    if not dump_file.exists():
        _fail(f"PostgreSQL dump missing: {dump_file}")
        passed = False
    else:
        expected_sha = pg_info.get("sha256", "")
        actual_sha = sha256_file(dump_file)
        if expected_sha and actual_sha != expected_sha:
            _fail(
                f"PostgreSQL dump SHA-256 mismatch: "
                f"expected={expected_sha[:16]}…  actual={actual_sha[:16]}…"
            )
            passed = False
        else:
            size = dump_file.stat().st_size
            _ok(
                f"PostgreSQL dump OK  size={size // 1_048_576} MB  "
                f"sha256={actual_sha[:16]}…"
            )

    # ------------------------------------------------------------------ #
    # 3. CAS                                                               #
    # ------------------------------------------------------------------ #
    cas_info = manifest.get("cas", {})
    expected_objects = cas_info.get("objects", 0)
    expected_bytes = cas_info.get("bytes", 0)

    cas_root = backup_path / "cas" / "objects"
    if not cas_root.exists():
        _fail("CAS directory missing: cas/objects/")
        passed = False
    else:
        all_cas_files = [p for p in cas_root.rglob("*") if p.is_file()]
        actual_objects = len(all_cas_files)
        actual_bytes = sum(p.stat().st_size for p in all_cas_files)

        if actual_objects != expected_objects:
            _fail(
                f"CAS file count mismatch: "
                f"expected={expected_objects}  actual={actual_objects}"
            )
            passed = False
        else:
            _ok(f"CAS file count OK: {actual_objects} objects")

        if actual_bytes != expected_bytes:
            _fail(
                f"CAS total bytes mismatch: "
                f"expected={expected_bytes}  actual={actual_bytes}"
            )
            passed = False
        else:
            _ok(f"CAS total bytes OK: {actual_bytes // 1_048_576} MB")

        # Path/hash consistency: filename must equal sha256 of content
        hash_errors = 0
        hash_checked = 0
        for p in all_cas_files:
            leaf = p.name
            if not _SHA256_RE.fullmatch(leaf):
                _fail(f"CAS file with non-SHA256 name: {p}")
                hash_errors += 1
                passed = False
                continue

            if verify_all_hashes:
                actual = sha256_file(p)
                if actual != leaf:
                    _fail(f"CAS hash mismatch: {p.relative_to(cas_root)}")
                    hash_errors += 1
                    passed = False
                hash_checked += 1

        if verify_all_hashes:
            if hash_errors == 0:
                _ok(f"CAS SHA-256 verified: {hash_checked} files")
            else:
                _fail(f"CAS SHA-256 errors: {hash_errors}")
        else:
            _ok("CAS filename/path structure OK (use --verify-all-hashes for full SHA-256 check)")

    # ------------------------------------------------------------------ #
    # 4. Qdrant snapshot (if present)                                      #
    # ------------------------------------------------------------------ #
    qdrant_info = manifest.get("qdrant", {})
    snap_rel = qdrant_info.get("snapshot_file")
    if snap_rel:
        snap_file = backup_path / snap_rel
        if not snap_file.exists():
            _fail(f"Qdrant snapshot missing: {snap_file}")
            passed = False
        else:
            expected_sha = qdrant_info.get("snapshot_sha256", "")
            actual_sha = sha256_file(snap_file)
            if expected_sha and actual_sha != expected_sha:
                _fail(
                    f"Qdrant snapshot SHA-256 mismatch: "
                    f"expected={expected_sha[:16]}…  actual={actual_sha[:16]}…"
                )
                passed = False
            else:
                size = snap_file.stat().st_size
                _ok(
                    f"Qdrant snapshot OK  size={size // 1_048_576} MB  "
                    f"sha256={actual_sha[:16]}…"
                )
    else:
        status = qdrant_info.get("status", "?")
        logger.info("NOTE  Qdrant snapshot not present (status=%s) — rebuild from source of truth", status)

    # ------------------------------------------------------------------ #
    # 5. Metadata files                                                    #
    # ------------------------------------------------------------------ #
    for mf in ["metadata/git.txt", "metadata/alembic.txt", "metadata/environment.json"]:
        p = backup_path / mf
        if not p.exists():
            _fail(f"Metadata file missing: {mf}")
            passed = False
        else:
            _ok(f"Metadata present: {mf}")

    # ------------------------------------------------------------------ #
    # 6. No .partial marker                                                #
    # ------------------------------------------------------------------ #
    backup_name = backup_path.name
    partial_marker = backup_path.parent / f"{backup_name}.partial"
    if partial_marker.exists():
        _fail("Backup has .partial marker — it did not complete successfully")
        passed = False
    else:
        _ok("No .partial marker — backup completed")

    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a WorkMind backup (read-only)"
    )
    parser.add_argument("--backup-path", required=True)
    parser.add_argument(
        "--verify-all-hashes", action="store_true",
        help="Compute SHA-256 of every CAS file (slow for large CAS).",
    )
    args = parser.parse_args(argv)

    backup_path = Path(args.backup_path)
    if not backup_path.exists():
        logger.error("Backup path not found: %s", backup_path)
        return 1

    logger.info("=" * 60)
    logger.info("WorkMind Backup Verification")
    logger.info("  Path: %s", backup_path)
    logger.info("=" * 60)

    ok = verify_backup(backup_path, verify_all_hashes=args.verify_all_hashes)

    logger.info("=" * 60)
    if ok:
        logger.info("BACKUP VERIFY: PASS")
    else:
        logger.error("BACKUP VERIFY: FAIL")
    logger.info("=" * 60)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
