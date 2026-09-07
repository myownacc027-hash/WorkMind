from pathlib import Path

from packages.common.config import Settings


def test_document_paths_and_extensions(tmp_path: Path) -> None:
    settings = Settings(
        workmind_home=tmp_path,
        document_supported_extensions_csv="pdf,pptx,md",
    )
    assert settings.document_supported_extensions == {
        ".pdf",
        ".pptx",
        ".md",
    }
    assert settings.document_import_dir == tmp_path / "import"
    assert settings.document_archive_dir == (
        tmp_path / "archive" / "documents"
    )
    assert settings.document_text_dir == (
        tmp_path / "processed" / "documents" / "text"
    )
