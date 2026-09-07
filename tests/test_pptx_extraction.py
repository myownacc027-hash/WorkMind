from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from packages.processing.attachments import extract_document


def test_pptx_text_and_table_are_extracted(tmp_path: Path) -> None:
    presentation = Presentation()
    slide = presentation.slides.add_slide(
        presentation.slide_layouts[5]
    )
    slide.shapes.title.text = "Проект WorkMind"
    textbox = slide.shapes.add_textbox(
        Inches(1),
        Inches(1.5),
        Inches(5),
        Inches(1),
    )
    textbox.text_frame.text = "Решение принято 21 июля"
    table = slide.shapes.add_table(
        2,
        2,
        Inches(1),
        Inches(3),
        Inches(5),
        Inches(1.5),
    ).table
    table.cell(0, 0).text = "Задача"
    table.cell(0, 1).text = "Статус"
    table.cell(1, 0).text = "189422"
    table.cell(1, 1).text = "Доработка"

    path = tmp_path / "test.pptx"
    presentation.save(path)

    result = extract_document(
        path,
        max_bytes=10_000_000,
        max_chars=100_000,
        supported_extensions={".pptx"},
    )
    assert result.status == "extracted"
    assert result.method == "python-pptx"
    assert result.metadata["slide_count"] == 1
    assert "Проект WorkMind" in result.text
    assert "Решение принято 21 июля" in result.text
    assert "189422" in result.text
    assert "Доработка" in result.text
