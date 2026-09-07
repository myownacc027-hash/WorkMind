
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.config import Settings
from packages.database.models import Attachment, StoredObject


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StorageIntegrityError(RuntimeError):
    pass


def sha256_file(
    path: Path,
    *,
    chunk_size: int = 8 * 1024 * 1024,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def object_store_root(settings: Settings) -> Path:
    return settings.workmind_home / "archive" / "objects"


def object_path(
    settings: Settings,
    sha256: str,
) -> Path:
    digest = sha256.strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(
            f"Invalid SHA-256 digest: {sha256!r}"
        )
    return (
        object_store_root(settings)
        / digest[:2]
        / digest[2:4]
        / digest
    )


def resolve_attachment_path(
    attachment: Attachment,
) -> Path | None:
    stored_object = getattr(
        attachment,
        "stored_object",
        None,
    )
    if (
        stored_object is not None
        and stored_object.storage_path
    ):
        return Path(stored_object.storage_path)

    if attachment.archive_path:
        return Path(attachment.archive_path)

    return None


def _verify_file(
    path: Path,
    *,
    expected_hash: str,
    expected_size: int,
) -> None:
    if not path.exists() or not path.is_file():
        raise StorageIntegrityError(
            f"Stored object is missing: {path}"
        )

    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise StorageIntegrityError(
            "Stored object size mismatch: "
            f"{path}; expected={expected_size}, "
            f"actual={actual_size}"
        )

    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise StorageIntegrityError(
            "Stored object SHA-256 mismatch: "
            f"{path}; expected={expected_hash}, "
            f"actual={actual_hash}"
        )


def _adopt_file(
    source: Path,
    destination: Path,
    *,
    expected_hash: str,
    expected_size: int,
) -> None:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if destination.exists():
        _verify_file(
            destination,
            expected_hash=expected_hash,
            expected_size=expected_size,
        )
        source.unlink(missing_ok=True)
        return

    temporary = destination.with_name(
        destination.name
        + "."
        + uuid.uuid4().hex
        + ".partial"
    )

    try:
        try:
            os.replace(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
            source.unlink()

        _verify_file(
            temporary,
            expected_hash=expected_hash,
            expected_size=expected_size,
        )

        if destination.exists():
            _verify_file(
                destination,
                expected_hash=expected_hash,
                expected_size=expected_size,
            )
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def store_attachment_file(
    session: Session,
    *,
    settings: Settings,
    source_path: Path,
) -> StoredObject:
    source = source_path.resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(source)

    size = source.stat().st_size
    digest = sha256_file(source)

    existing = session.scalar(
        select(StoredObject).where(
            StoredObject.sha256 == digest
        )
    )

    if existing is not None:
        if int(existing.size_bytes) != size:
            raise StorageIntegrityError(
                "StoredObject size conflicts with SHA-256 "
                f"{digest}: db={existing.size_bytes}, "
                f"incoming={size}"
            )

        destination = Path(existing.storage_path)
        if destination.exists():
            _verify_file(
                destination,
                expected_hash=digest,
                expected_size=size,
            )
            source.unlink(missing_ok=True)
        else:
            _adopt_file(
                source,
                destination,
                expected_hash=digest,
                expected_size=size,
            )
        return existing

    destination = object_path(
        settings,
        digest,
    )
    _adopt_file(
        source,
        destination,
        expected_hash=digest,
        expected_size=size,
    )

    stored_object = StoredObject(
        sha256=digest,
        size_bytes=size,
        storage_path=str(destination),
    )
    session.add(stored_object)
    session.flush()
    return stored_object
