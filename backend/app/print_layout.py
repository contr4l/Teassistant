"""Reusable print templates and source table/workspace formatting helpers."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

LayoutMode = Literal["answer_space", "compact"]
TEMPLATE_VERSION = "exam-print-v1"

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / TEMPLATE_VERSION
_TEMPLATE_NAMES = {
    (False, "answer_space"): "student-answer-space.docx",
    (False, "compact"): "student-compact.docx",
    (True, "answer_space"): "teacher.docx",
    (True, "compact"): "teacher.docx",
}
_NUMERIC_VALUE = re.compile(r"^[+\-−]?(?:\d+(?:\.\d*)?|\.\d+)(?:%|°)?$")


def _set_style_font(style, *, east_asia: str, size: float, bold: bool = False) -> None:
    style.font.name = "Times New Roman"
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    r_pr = style._element.get_or_add_rPr()
    fonts = r_pr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    fonts.set(qn("w:ascii"), "Times New Roman")
    fonts.set(qn("w:hAnsi"), "Times New Roman")
    fonts.set(qn("w:eastAsia"), east_asia)
    fonts.set(qn("w:cs"), "Times New Roman")
    p_pr = style._element.pPr
    if p_pr is not None:
        paragraph_borders = p_pr.find(qn("w:pBdr"))
        if paragraph_borders is not None:
            p_pr.remove(paragraph_borders)


def _style(document, name: str, *, base: str = "Normal"):
    styles = document.styles
    try:
        style = styles[name]
    except KeyError:
        style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    if name != "Normal":
        style.base_style = styles[base]
    return style


def _set_field(paragraph, instruction: str) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction_text = OxmlElement("w:instrText")
    instruction_text.set(qn("xml:space"), "preserve")
    instruction_text.text = f" {instruction} "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction_text, separate, result, end):
        run._r.append(element)


def _add_page_footer(document: DocumentObject) -> None:
    footer = document.sections[0].footer
    paragraph = footer.paragraphs[0]
    paragraph.style = document.styles["ExamMeta"]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run("第 ")
    _set_field(paragraph, "PAGE")
    paragraph.add_run(" 页 / 共 ")
    _set_field(paragraph, "NUMPAGES")
    paragraph.add_run(" 页")


def _build_template(
    *, teacher: bool, layout: LayoutMode, is_synthetic_student: bool
) -> DocumentObject:
    document = Document()
    for section in document.sections:
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Mm(18)
        section.bottom_margin = Mm(18)
        section.left_margin = Mm(18)
        section.right_margin = Mm(18)
        section.header_distance = Mm(8)
        section.footer_distance = Mm(8)

    normal = document.styles["Normal"]
    _set_style_font(normal, east_asia="宋体", size=11)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.12

    specs = {
        "Title": ("Normal", "宋体", 16, True),
        "ExamMeta": ("Normal", "宋体", 9, False),
        "ExamSection": ("Normal", "黑体", 13, True),
        "ExamQuestion": ("Normal", "宋体", 11, False),
        "ExamBody": ("Normal", "宋体", 11, False),
        "ExamOption": ("Normal", "宋体", 10.5, False),
        "ExamAnswer": ("Normal", "宋体", 10.5, False),
        "ExamWorkSpace": ("Normal", "宋体", 9, False),
    }
    for name, (base, east_asia, size, bold) in specs.items():
        style = _style(document, name, base=base)
        _set_style_font(style, east_asia=east_asia, size=size, bold=bold)

    title_style = document.styles["Title"]
    title_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_style.paragraph_format.space_before = Pt(0)
    title_style.paragraph_format.space_after = Pt(5)
    title_style.paragraph_format.keep_with_next = True

    meta_style = document.styles["ExamMeta"]
    meta_style.font.color.rgb = RGBColor(90, 90, 90)
    meta_style.paragraph_format.space_after = Pt(4)
    meta_style.paragraph_format.keep_with_next = True

    section_style = document.styles["ExamSection"]
    section_style.paragraph_format.space_before = Pt(9)
    section_style.paragraph_format.space_after = Pt(5)
    section_style.paragraph_format.keep_with_next = True

    question_style = document.styles["ExamQuestion"]
    question_style.paragraph_format.left_indent = Mm(8)
    question_style.paragraph_format.first_line_indent = Mm(-8)
    question_style.paragraph_format.space_after = Pt(4)
    question_style.paragraph_format.widow_control = True

    body_style = document.styles["ExamBody"]
    body_style.paragraph_format.space_after = Pt(4)
    body_style.paragraph_format.widow_control = True

    option_style = document.styles["ExamOption"]
    option_style.paragraph_format.left_indent = Mm(4)
    option_style.paragraph_format.space_after = Pt(2)

    answer_style = document.styles["ExamAnswer"]
    answer_style.paragraph_format.space_after = Pt(2)
    answer_style.paragraph_format.widow_control = True

    workspace_style = document.styles["ExamWorkSpace"]
    workspace_style.paragraph_format.space_before = Pt(0)
    workspace_style.paragraph_format.space_after = Pt(1)
    workspace_style.paragraph_format.line_spacing = Pt(15)
    workspace_style.paragraph_format.keep_together = True

    title = "教师答案与解析" if teacher else "数学练习"
    document.add_paragraph(title, style="Title")
    meta = document.add_paragraph(style="ExamMeta")
    meta.add_run("姓名：________________    日期：____________")
    if not teacher:
        meta.add_run("    版式：作答版" if layout == "answer_space" else "    版式：紧凑版")
    if is_synthetic_student:
        marker = document.add_paragraph(style="ExamMeta")
        marker.paragraph_format.keep_with_next = True
        marker.add_run("合成学生进度演示 · 非真实学生")

    _add_page_footer(document)
    return document


def make_exam_document(
    *, teacher: bool, layout: LayoutMode, is_synthetic_student: bool
) -> DocumentObject:
    """Load the versioned blank Word template for a student or teacher document."""
    if layout not in {"answer_space", "compact"}:
        raise ValueError("unsupported exam layout mode")
    template_name = _TEMPLATE_NAMES[(teacher, layout)]
    template_path = _TEMPLATE_DIR / template_name
    if not template_path.is_file():
        raise FileNotFoundError(
            f"print template missing: {template_path}; run scripts/build_print_templates.py"
        )
    document = Document(template_path)
    # The synthetic marker is an explicit per-output choice, not a template default.
    if is_synthetic_student:
        marker_exists = any(
            paragraph.text == "合成学生进度演示 · 非真实学生" for paragraph in document.paragraphs
        )
        if not marker_exists:
            marker = document.add_paragraph(style="ExamMeta")
            marker.add_run("合成学生进度演示 · 非真实学生")
    else:
        for paragraph in list(document.paragraphs):
            if paragraph.text == "合成学生进度演示 · 非真实学生":
                paragraph._element.getparent().remove(paragraph._element)
    return document


def _is_numeric_column(table, column_index: int) -> bool:
    values = [cell.text.strip() for row in table.rows for cell in [row.cells[column_index]]]
    if len(values) > 1 and values[0] and not _NUMERIC_VALUE.fullmatch(values[0]):
        values = values[1:]
    present = [value for value in values if value]
    return bool(present) and all(_NUMERIC_VALUE.fullmatch(value) for value in present)


def style_source_table(table) -> None:
    """Set readable source-table widths and alignment without changing cell content."""
    column_count = len(table.columns)
    if column_count == 0:
        return
    table.autofit = False
    available_mm = 174.0
    numeric = [_is_numeric_column(table, index) for index in range(column_count)]
    text_column = next((index for index, is_numeric in enumerate(numeric) if not is_numeric), None)
    if text_column is None:
        weights = [1.0] * column_count
    elif column_count == 1:
        weights = [1.0]
    else:
        # Allocate more of the printable width to the first descriptive column.
        weights = [
            0.34 if index == text_column else 0.66 / (column_count - 1)
            for index in range(column_count)
        ]
    for index, weight in enumerate(weights):
        width = Mm(available_mm * weight)
        table.columns[index].width = width
        for row in table.rows:
            cell = row.cells[index]
            cell.width = width
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                paragraph.alignment = (
                    WD_ALIGN_PARAGRAPH.CENTER if numeric[index] else WD_ALIGN_PARAGRAPH.LEFT
                )


def _add_bottom_rule(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    for edge in ("bottom", "between"):
        rule = OxmlElement(f"w:{edge}")
        rule.set(qn("w:val"), "single")
        rule.set(qn("w:sz"), "4")
        rule.set(qn("w:space"), "1")
        rule.set(qn("w:color"), "B7B7B7")
        borders.append(rule)


def append_answer_space(
    document: DocumentObject,
    *,
    question_type: str,
    layout: LayoutMode,
    subquestion_count: int = 1,
) -> None:
    """Append ruled response lines sized by free-response subquestion count."""
    if layout not in {"answer_space", "compact"}:
        raise ValueError("unsupported exam layout mode")
    if type(subquestion_count) is not int or subquestion_count < 1:
        raise ValueError("subquestion_count must be a positive integer")
    if layout == "compact" or question_type != "free_response":
        return

    # About 40 mm for one short computation and 74 mm for a three-part solution.
    target_mm = 22.0 + 16.0 * min(subquestion_count, 6)
    line_mm = 5.65
    line_count = max(1, math.ceil(target_mm / line_mm))
    for _ in range(line_count):
        paragraph = document.add_paragraph(style="ExamWorkSpace")
        paragraph.add_run(" ")
        _add_bottom_rule(paragraph)
