
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from packages.processing.attachments import extract_attachment
from packages.storage import (
    object_path,
    resolve_attachment_path,
    sha256_file,
)


def test_object_path_is_content_addressed(tmp_path: Path) -> None:
    settings = SimpleNamespace(workmind_home=tmp_path)
    digest = "a" * 64

    assert object_path(settings, digest) == (
        tmp_path
        / "archive"
        / "objects"
        / "aa"
        / "aa"
        / digest
    )


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(b"workmind")

    assert sha256_file(path) == (
        "da219491884719083c01c24251af7add"
        "76b3e98f19fd7c1b3ff310a04ab4e9af"
    )


def test_resolve_attachment_path_prefers_stored_object(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "object"
    legacy = tmp_path / "legacy"

    attachment = SimpleNamespace(
        stored_object=SimpleNamespace(
            storage_path=str(canonical)
        ),
        archive_path=str(legacy),
    )

    assert resolve_attachment_path(
        attachment
    ) == canonical


def test_resolve_attachment_path_falls_back_to_legacy(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"

    attachment = SimpleNamespace(
        stored_object=None,
        archive_path=str(legacy),
    )

    assert resolve_attachment_path(
        attachment
    ) == legacy


def test_extension_hint_allows_extensionless_blob(
    tmp_path: Path,
) -> None:
    blob = tmp_path / ("f" * 64)
    blob.write_text("hello workmind", encoding="utf-8")

    result = extract_attachment(
        blob,
        max_bytes=1024,
        max_chars=1024,
        supported_extensions={".txt"},
        filename_hint="notes.txt",
    )

    assert result.status == "extracted"
    assert "hello workmind" in result.text
