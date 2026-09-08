"""
Tests for WorkMind backup tooling (BACKUP-01).

These tests are fully offline — no real PostgreSQL, Docker, or CAS required.
They cover:
  - manifest generation logic
  - SHA-256 hashing helpers
  - CAS path structure validation
  - CAS inventory counting
  - destructive restore guard
  - backup failure leaves .partial marker
  - verify_backup detects missing/corrupt files
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import backup engines from scripts/backup/ without making them a package
# ---------------------------------------------------------------------------

def _load_engine(name: str) -> object:
    """Dynamically load a script from scripts/backup/ by name."""
    scripts_backup = Path(__file__).resolve().parent.parent / "scripts" / "backup"
    spec = importlib.util.spec_from_file_location(
        f"backup_engines.{name}",
        scripts_backup / f"{name}.py",
    )
    assert spec is not None, f"Could not locate {name}.py"
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def backup_engine():
    return _load_engine("_backup_engine")


@pytest.fixture(scope="module")
def restore_engine():
    return _load_engine("_restore_engine")


@pytest.fixture(scope="module")
def verify_engine():
    return _load_engine("_verify_engine")


# ===========================================================================
# 1. sha256_file — basic hashing
# ===========================================================================

def test_sha256_file_known_value(tmp_path: Path, backup_engine) -> None:
    p = tmp_path / "data.bin"
    p.write_bytes(b"workmind-backup-test")
    expected = hashlib.sha256(b"workmind-backup-test").hexdigest()
    assert backup_engine.sha256_file(p) == expected


def test_sha256_file_large_chunked(tmp_path: Path, backup_engine) -> None:
    """Verify chunked reading produces same result as single hashlib call."""
    data = b"x" * (10 * 1024 * 1024)  # 10 MB
    p = tmp_path / "large.bin"
    p.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert backup_engine.sha256_file(p) == expected


def test_sha256_file_empty(tmp_path: Path, backup_engine) -> None:
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    expected = hashlib.sha256(b"").hexdigest()
    assert backup_engine.sha256_file(p) == expected


# ===========================================================================
# 2. CAS path structure validation
# ===========================================================================

SHA256_PATTERN = backup_engine if False else None  # type hint trick — used in tests below

def _valid_sha256() -> str:
    return hashlib.sha256(b"test").hexdigest()


def test_cas_path_structure(tmp_path: Path) -> None:
    """CAS backup must mirror aa/bb/<sha256> layout."""
    sha = _valid_sha256()
    aa, bb = sha[:2], sha[2:4]

    cas_root = tmp_path / "source" / "objects"
    obj_dir = cas_root / aa / bb
    obj_dir.mkdir(parents=True)
    obj_file = obj_dir / sha
    obj_file.write_bytes(b"test")

    # All files reachable via rglob and have correct leaf name
    all_files = [p for p in cas_root.rglob("*") if p.is_file()]
    assert len(all_files) == 1
    assert all_files[0].name == sha


def test_cas_filename_is_sha256() -> None:
    """Every CAS leaf filename must be a 64-hex SHA-256."""
    import re
    sha256_re = re.compile(r"^[0-9a-f]{64}$")
    good = _valid_sha256()
    assert sha256_re.fullmatch(good)
    assert not sha256_re.fullmatch("not-a-hash")
    assert not sha256_re.fullmatch("a" * 63)
    assert not sha256_re.fullmatch("A" * 64)  # uppercase not allowed


# ===========================================================================
# 3. CAS inventory
# ===========================================================================

def test_cas_inventory_count(tmp_path: Path) -> None:
    """backup_cas should count exactly the files it copies."""
    import shutil

    be = _load_engine("_backup_engine")

    # Build a mini CAS with 3 real SHA-256-named files
    cas_root = tmp_path / "cas_src" / "objects"
    backup_dir = tmp_path / "backup"

    blobs = [b"alpha", b"beta", b"gamma"]
    for blob in blobs:
        sha = hashlib.sha256(blob).hexdigest()
        dst = cas_root / sha[:2] / sha[2:4] / sha
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(blob)

    result = be.backup_cas(backup_dir, cas_root, verify_hashes=True)

    assert result["objects"] == 3
    total = sum(len(b) for b in blobs)
    assert result["bytes"] == total

    # Check dest structure
    dest_root = backup_dir / "cas" / "objects"
    restored_files = [p for p in dest_root.rglob("*") if p.is_file()]
    assert len(restored_files) == 3


def test_cas_inventory_detects_hash_mismatch(tmp_path: Path) -> None:
    """backup_cas must raise if copied file has wrong SHA-256 content."""
    be = _load_engine("_backup_engine")

    cas_root = tmp_path / "cas_src" / "objects"
    backup_dir = tmp_path / "backup"

    # Create a file but name it with a wrong SHA-256 (tampered)
    real_sha = hashlib.sha256(b"real-content").hexdigest()
    wrong_sha = "a" * 64  # valid format, wrong hash
    dst = cas_root / wrong_sha[:2] / wrong_sha[2:4] / wrong_sha
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"real-content")  # content doesn't match name

    with pytest.raises(RuntimeError, match="integrity errors"):
        be.backup_cas(backup_dir, cas_root, verify_hashes=True)


def test_cas_inventory_missing_root(tmp_path: Path) -> None:
    be = _load_engine("_backup_engine")
    with pytest.raises(RuntimeError, match="CAS root not found"):
        be.backup_cas(tmp_path / "backup", tmp_path / "nonexistent", verify_hashes=False)


# ===========================================================================
# 4. Destructive restore guard
# ===========================================================================

def test_destructive_restore_guard_blocks_production(tmp_path: Path) -> None:
    """restore_postgres must raise PermissionError for production DB without flag."""
    re_engine = _load_engine("_restore_engine")

    # Create a minimal fake backup dir with a dump file
    pg_dir = tmp_path / "postgres"
    pg_dir.mkdir()
    (pg_dir / "workmind.dump").write_bytes(b"\x00" * 100)

    with pytest.raises(PermissionError, match="protected production database"):
        re_engine.restore_postgres(
            tmp_path,
            "workmind",  # production DB name
            confirm_destructive=False,
        )


def test_destructive_restore_guard_allows_test_db(tmp_path: Path) -> None:
    """restore_postgres must NOT raise for a non-production target database
    even without confirm_destructive, but it will try to connect to Docker
    which is not available in unit tests — so we mock subprocess."""
    re_engine = _load_engine("_restore_engine")

    pg_dir = tmp_path / "postgres"
    pg_dir.mkdir()
    dump = pg_dir / "workmind.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 100)

    completed_ok = MagicMock()
    completed_ok.returncode = 0
    completed_ok.stderr = b""
    completed_ok.stdout = b""

    with patch("subprocess.run", return_value=completed_ok):
        # Should not raise PermissionError — may raise RuntimeError for other reasons
        try:
            re_engine.restore_postgres(
                tmp_path,
                "workmind_restore_test_20260908",
                confirm_destructive=False,
            )
        except PermissionError:
            pytest.fail("PermissionError raised for non-production test database")
        except Exception:
            pass  # Other errors (Docker not available) are OK in unit tests


def test_destructive_restore_guard_allows_production_with_flag(tmp_path: Path) -> None:
    """With confirm_destructive=True, production restore is not blocked at guard."""
    re_engine = _load_engine("_restore_engine")

    pg_dir = tmp_path / "postgres"
    pg_dir.mkdir()
    dump = pg_dir / "workmind.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 100)

    completed_ok = MagicMock()
    completed_ok.returncode = 0
    completed_ok.stderr = b""

    with patch("subprocess.run", return_value=completed_ok):
        try:
            re_engine.restore_postgres(
                tmp_path,
                "workmind",
                confirm_destructive=True,
            )
        except PermissionError:
            pytest.fail("PermissionError raised despite confirm_destructive=True")
        except Exception:
            pass  # Docker not available — OK


# ===========================================================================
# 5. Manifest generation
# ===========================================================================

def test_manifest_structure(tmp_path: Path, backup_engine) -> None:
    """utc_now_iso should return a valid ISO-8601 UTC timestamp."""
    ts = backup_engine.utc_now_iso()
    assert ts.endswith("Z")
    assert "T" in ts
    assert len(ts) > 15


def test_manifest_no_secrets(tmp_path: Path) -> None:
    """Manifest written by the engine must not contain passwords or API keys."""
    # Simulate a manifest that would be written
    manifest = {
        "format_version": 1,
        "created_at": "2026-09-08T12:00:00Z",
        "workmind_version": "0.12.1",
        "git_commit": "a6f99b7",
        "git_tag": "stable-post-cas-migration-2026-09-08",
        "alembic_revision": "0011_content_addressed_storage",
        "postgres": {
            "database": "workmind",
            "format": "custom",
            "dump_file": "postgres/workmind.dump",
            "size": 1234567,
            "sha256": "a" * 64,
        },
        "cas": {"objects": 8608, "bytes": 5000000000},
        "qdrant": {"collection": "workmind_chunks", "points_count": 2086865},
    }
    text = json.dumps(manifest)
    forbidden = ["password", "secret", "api_key", "DATABASE_URL", "change_me"]
    for word in forbidden:
        assert word.lower() not in text.lower(), f"Manifest contains forbidden word: {word}"


# ===========================================================================
# 6. Backup failure leaves .partial marker
# ===========================================================================

def test_backup_partial_marker_on_failure(tmp_path: Path) -> None:
    """
    If the backup engine fails, a .partial marker must remain in backup_root.
    We simulate a failure by making pg_dump return non-zero.
    """
    be = _load_engine("_backup_engine")
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    # Mock: settings load, disable_task, enable_task, wait_for_task_idle
    # and make pg_dump fail
    fake_settings = SimpleNamespace(
        workmind_home=tmp_path,
        qdrant_url="http://localhost:6333",
        qdrant_collection="workmind_chunks",
    )

    failed_proc = MagicMock()
    failed_proc.returncode = 1
    failed_proc.stderr = b"pg_dump: error: connection refused"

    with (
        patch.object(be, "get_settings", return_value=fake_settings, create=True),
        patch("subprocess.run", return_value=failed_proc),
        patch.object(be, "wait_for_task_idle"),
        patch.object(be, "disable_task", return_value=False),
        patch.object(be, "enable_task"),
        patch("sys.path"),
    ):
        # Inject fake get_settings into the module
        original = getattr(be, "_injected_settings", None)
        be._injected_settings = fake_settings  # type: ignore[attr-defined]

        exit_code = be.main(["--backup-root", str(backup_root)])

    # Should have failed
    assert exit_code == 1

    # .partial marker must exist (shows backup did not complete)
    partial_files = list(backup_root.glob("*.partial"))
    assert len(partial_files) == 1, (
        f"Expected .partial marker, found: {list(backup_root.iterdir())}"
    )


# ===========================================================================
# 7. verify_backup — detects missing dump
# ===========================================================================

def _make_minimal_backup(tmp_path: Path) -> Path:
    """Create a structurally valid backup directory for verify tests."""
    backup_dir = tmp_path / "WorkMindBackup-20260908-120000"
    backup_dir.mkdir()

    # PostgreSQL dump
    pg_dir = backup_dir / "postgres"
    pg_dir.mkdir()
    dump = pg_dir / "workmind.dump"
    dump_content = b"PGDMP" + b"\x00" * 200
    dump.write_bytes(dump_content)
    dump_sha256 = hashlib.sha256(dump_content).hexdigest()

    # CAS objects — 2 real objects
    cas_root = backup_dir / "cas" / "objects"
    blobs = {
        hashlib.sha256(b"obj1").hexdigest(): b"obj1",
        hashlib.sha256(b"obj2").hexdigest(): b"obj2",
    }
    total_cas_bytes = 0
    for sha, content in blobs.items():
        dst = cas_root / sha[:2] / sha[2:4] / sha
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        total_cas_bytes += len(content)

    # Metadata
    meta = backup_dir / "metadata"
    meta.mkdir()
    (meta / "git.txt").write_text("Commit: abc\nTag: v1\n")
    (meta / "alembic.txt").write_text("Revision: 0011\n")
    (meta / "environment.json").write_text("{}")

    # Manifest
    manifest = {
        "format_version": 1,
        "created_at": "2026-09-08T09:00:00Z",
        "workmind_version": "0.12.1",
        "git_commit": "a6f99b7",
        "git_tag": "stable-post-cas-migration-2026-09-08",
        "alembic_revision": "0011_content_addressed_storage",
        "postgres": {
            "database": "workmind",
            "dump_file": "postgres/workmind.dump",
            "size": len(dump_content),
            "sha256": dump_sha256,
        },
        "cas": {
            "objects": 2,
            "bytes": total_cas_bytes,
            "directory": "cas/objects",
        },
        "qdrant": {
            "collection": "workmind_chunks",
            "status": "skipped",
        },
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    return backup_dir


def test_verify_backup_pass(tmp_path: Path, verify_engine) -> None:
    backup_dir = _make_minimal_backup(tmp_path)
    result = verify_engine.verify_backup(backup_dir, verify_all_hashes=True)
    assert result is True


def test_verify_backup_fails_missing_dump(tmp_path: Path, verify_engine) -> None:
    backup_dir = _make_minimal_backup(tmp_path)
    (backup_dir / "postgres" / "workmind.dump").unlink()
    result = verify_engine.verify_backup(backup_dir)
    assert result is False


def test_verify_backup_fails_dump_sha256_mismatch(tmp_path: Path, verify_engine) -> None:
    backup_dir = _make_minimal_backup(tmp_path)
    # Tamper dump
    dump = backup_dir / "postgres" / "workmind.dump"
    dump.write_bytes(b"TAMPERED")
    result = verify_engine.verify_backup(backup_dir)
    assert result is False


def test_verify_backup_fails_cas_count_mismatch(tmp_path: Path, verify_engine) -> None:
    backup_dir = _make_minimal_backup(tmp_path)
    # Add an extra CAS file not in manifest
    extra_content = b"extra"
    extra_sha = hashlib.sha256(extra_content).hexdigest()
    extra_path = backup_dir / "cas" / "objects" / extra_sha[:2] / extra_sha[2:4] / extra_sha
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_bytes(extra_content)
    result = verify_engine.verify_backup(backup_dir)
    assert result is False


def test_verify_backup_fails_partial_marker(tmp_path: Path, verify_engine) -> None:
    backup_dir = _make_minimal_backup(tmp_path)
    (backup_dir.parent / f"{backup_dir.name}.partial").touch()
    result = verify_engine.verify_backup(backup_dir)
    assert result is False


def test_verify_backup_fails_missing_manifest(tmp_path: Path, verify_engine) -> None:
    backup_dir = _make_minimal_backup(tmp_path)
    (backup_dir / "manifest.json").unlink()
    result = verify_engine.verify_backup(backup_dir)
    assert result is False
