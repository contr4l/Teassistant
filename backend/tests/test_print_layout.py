from __future__ import annotations

from pathlib import Path

import pytest
from app.print_layout import (
    TEMPLATE_VERSION,
    append_answer_space,
    make_exam_document,
    style_source_table,
)
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "templates" / TEMPLATE_VERSION


def test_program_generated_template_files_are_loadable_and_empty():
    expected = {
        "student-answer-space.docx",
        "student-compact.docx",
        "teacher.docx",
    }
    assert {path.name for path in TEMPLATE_DIR.glob("*.docx")} == expected
    for path in TEMPLATE_DIR.glob("*.docx"):
        document = Document(path)
        assert document.paragraphs
        assert not any("Question" in paragraph.text for paragraph in document.paragraphs)
        assert abs(document.sections[0].page_width - Mm(210)) <= Mm(0.2)
        assert abs(document.sections[0].page_height - Mm(297)) <= Mm(0.2)
        assert abs(document.sections[0].left_margin - Mm(18)) <= Mm(0.2)
        assert abs(document.sections[0].right_margin - Mm(18)) <= Mm(0.2)
        assert document.paragraphs[0].style.name == "Title"
        assert document.paragraphs[0].style.font.size == Pt(16)


def test_make_exam_document_loads_mode_and_adds_only_explicit_synthetic_marker():
    regular = make_exam_document(teacher=False, layout="answer_space", is_synthetic_student=False)
    synthetic = make_exam_document(teacher=True, layout="compact", is_synthetic_student=True)
    assert "合成学生进度演示" not in "\n".join(p.text for p in regular.paragraphs)
    assert "合成学生进度演示 · 非真实学生" in "\n".join(p.text for p in synthetic.paragraphs)
    assert "姓名：" in regular.paragraphs[1].text
    assert "日期：" in regular.paragraphs[1].text
    assert "作答版" in regular.paragraphs[1].text
    compact = make_exam_document(teacher=False, layout="compact", is_synthetic_student=False)
    assert "紧凑版" in compact.paragraphs[1].text
    footer_xml = synthetic.sections[0].footer._element.xml
    assert "PAGE" in footer_xml
    assert "NUMPAGES" in footer_xml


def test_named_styles_have_print_font_and_semantic_roles():
    document = make_exam_document(teacher=False, layout="compact", is_synthetic_student=False)
    expected = {
        "Title",
        "ExamMeta",
        "ExamSection",
        "ExamQuestion",
        "ExamBody",
        "ExamOption",
        "ExamAnswer",
        "ExamWorkSpace",
    }
    assert expected <= {style.name for style in document.styles}
    body = document.styles["ExamBody"]
    assert body.font.size == Pt(11)
    r_fonts = body._element.rPr.rFonts
    assert (
        r_fonts.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ascii")
        == "Times New Roman"
    )
    assert (
        r_fonts.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia")
        == "宋体"
    )
    assert (
        document.styles["ExamSection"]._element.rPr.rFonts.get(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia"
        )
        == "黑体"
    )
    assert document.styles["ExamAnswer"].paragraph_format.space_after == Pt(2)


def test_answer_space_is_ruled_and_scales_with_subquestions():
    one = make_exam_document(teacher=False, layout="answer_space", is_synthetic_student=False)
    three = make_exam_document(teacher=False, layout="answer_space", is_synthetic_student=False)
    append_answer_space(
        one, question_type="free_response", layout="answer_space", subquestion_count=1
    )
    append_answer_space(
        three, question_type="free_response", layout="answer_space", subquestion_count=3
    )
    one_lines = [p for p in one.paragraphs if p.style.name == "ExamWorkSpace"]
    three_lines = [p for p in three.paragraphs if p.style.name == "ExamWorkSpace"]
    assert len(one_lines) == 7
    assert len(three_lines) > len(one_lines)
    assert all("w:pBdr" in paragraph._p.xml for paragraph in one_lines + three_lines)
    assert one.styles["ExamWorkSpace"].paragraph_format.line_spacing == Pt(15)


@pytest.mark.parametrize(
    ("question_type", "layout"),
    [("choice", "answer_space"), ("free_response", "compact")],
)
def test_non_response_layouts_do_not_insert_answer_space(question_type, layout):
    document = make_exam_document(teacher=False, layout=layout, is_synthetic_student=False)
    before = len(document.paragraphs)
    append_answer_space(
        document,
        question_type=question_type,
        layout=layout,
        subquestion_count=3,
    )
    assert len(document.paragraphs) == before


def test_source_table_keeps_content_and_allocates_numeric_columns():
    document = Document()
    table = document.add_table(rows=3, cols=3)
    data = [["项目", "数量", "比例"], ["班级", "42", "75%"], ["均值", "1.25", "0.5"]]
    for row, values in zip(table.rows, data, strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    before = [[cell.text for cell in row.cells] for row in table.rows]
    style_source_table(table)
    after = [[cell.text for cell in row.cells] for row in table.rows]
    widths = [column.width for column in table.columns]
    assert before == after
    assert widths[0] > widths[1]
    assert table.cell(1, 0).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert table.cell(1, 1).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER


@pytest.mark.parametrize("layout", ["invalid", "answer-space"])
def test_invalid_layout_is_rejected(layout):
    with pytest.raises(ValueError, match="unsupported exam layout mode"):
        make_exam_document(
            teacher=False,
            layout=layout,
            is_synthetic_student=False,
        )
