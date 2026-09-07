from pathlib import Path

from packages.processing.documents import (
    discover_files,
    safe_filename,
    sha256_file,
)


def test_discover_files_ignores_office_and_partial_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "~$draft.docx").write_bytes(b"temp")
    (tmp_path / "download.part").write_bytes(b"temp")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.md").write_text("beta", encoding="utf-8")

    names = [item.name for item in discover_files(tmp_path)]
    assert names == ["a.txt", "b.md"]


def test_sha256_and_safe_filename(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"workmind")
    assert sha256_file(path) == (
        "da219491884719083c01c24251af7add"
        "76b3e98f19fd7c1b3ff310a04ab4e9af"
    )
    assert safe_filename('bad<>:"/\\|?*name.txt') == "bad_________name.txt"
