"""Independent exam-layout checks using only explicit synthetic question fragments."""

import pytest
from docx.oxml.ns import qn


def field_codes(document):
    nodes = list(document.element.iter())
    for section in document.sections:
        nodes.extend(section.header._element.iter())
        nodes.extend(section.footer._element.iter())
    return " ".join(
        (node.text or "") if node.tag == qn("w:instrText") else node.get(qn("w:instr"), "")
        for node in nodes
    )


@pytest.mark.parametrize("layout", ["answer_space", "compact"])
def test_print_template_has_print_geometry_styles_and_real_page_field(layout):
    from app.print_layout import TEMPLATE_VERSION, make_exam_document

    document = make_exam_document(teacher=False, layout=layout, is_synthetic_student=True)
    assert TEMPLATE_VERSION == "exam-print-v1"
    section = document.sections[0]
    assert abs(section.page_width.mm - 210) < 0.2
    assert abs(section.page_height.mm - 297) < 0.2
    assert document.styles["Title"].font.size.pt == 16
    for name in [
        "ExamMeta",
        "ExamSection",
        "ExamQuestion",
        "ExamBody",
        "ExamOption",
        "ExamAnswer",
        "ExamWorkSpace",
    ]:
        assert name in document.styles
    body = document.styles["ExamBody"]
    assert body.font.size.pt == 11
    assert body.font.name == "Times New Roman"
    assert body.element.rPr.rFonts.get(qn("w:eastAsia")) == "宋体"
    assert document.styles["ExamOption"].font.size.pt == 10.5
    assert document.styles["ExamAnswer"].font.size.pt == 10.5
    assert document.styles["ExamAnswer"].paragraph_format.space_after.pt == 2
    text = "".join(document.element.itertext())
    assert "合成" in text and "姓名" in text and "日期" in text
    assert "PAGE" in field_codes(document).split(), "printed page numbers require an actual field"


def test_invalid_layout_is_rejected_before_creating_a_print_document():
    from app.print_layout import make_exam_document

    with pytest.raises((ValueError, TypeError)):
        make_exam_document(
            teacher=False, layout="a-layout-not-in-contract", is_synthetic_student=True
        )


def test_answer_space_is_added_for_free_response_only_and_scales_with_subquestions():
    from app.print_layout import append_answer_space, make_exam_document

    def workspace(layout, question_type, subquestions):
        document = make_exam_document(teacher=False, layout=layout, is_synthetic_student=True)
        before = len(document.paragraphs)
        append_answer_space(
            document, question_type=question_type, layout=layout, subquestion_count=subquestions
        )
        return document.paragraphs[before:]

    short = workspace("answer_space", "free_response", 1)
    long = workspace("answer_space", "free_response", 3)
    assert short and long
    assert len(long) > len(short)
    assert all(p.style.name == "ExamWorkSpace" for p in short + long)
    assert workspace("compact", "free_response", 3) == []
    assert workspace("answer_space", "choice", 1) == []


def test_table_styling_preserves_all_three_by_eight_cells_and_numeric_alignment():
    from app.print_layout import style_source_table
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    document = Document()
    table = document.add_table(rows=3, cols=8)
    expected = [
        ["射击次数", "20", "40", "100", "200", "400", "1000", "2000"],
        ["命中次数", "15", "33", "78", "158", "322", "801", "1596"],
        ["命中频率", "0.750", "0.825", "0.780", "0.790", "0.805", "0.801", "0.798"],
    ]
    for row, values in zip(table.rows, expected, strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    style_source_table(table)
    assert [[cell.text for cell in row.cells] for row in table.rows] == expected
    assert len(table.rows) == 3 and len(table.columns) == 8
    assert table.columns[0].width > table.columns[1].width
    for row in table.rows:
        for cell in row.cells[1:]:
            assert all(p.alignment == WD_ALIGN_PARAGRAPH.CENTER for p in cell.paragraphs)


@pytest.mark.parametrize(
    "fragment",
    [
        "<p>保留这段<span>【答案】为题中文字</span></p>",
        "<p><span><b>保留这段</b></span>【答案】为题中文字</p>",
        '<p><img src="media/synthetic.png"/>【答案】为图后数学文字</p>',
    ],
)
def test_prefix_cleaning_never_scans_past_visible_math_content(fragment):
    import re

    from app.source_exporting import _strip_element_prefix
    from lxml import html

    element = html.fragment_fromstring(fragment)
    before = html.tostring(element)
    assert _strip_element_prefix(element, re.compile(r"^\s*【答案】\s*")) is False
    assert html.tostring(element) == before


@pytest.mark.parametrize(
    "kind", ["no_options", "missing_option", "long_options", "formula_options"]
)
def test_option_grouping_never_changes_content_order_or_formula_metadata(kind):
    from copy import deepcopy

    from app.source_exporting import _question_units

    stem = {"text": "7. Synthetic question", "html": "<p>7. Synthetic question</p>"}
    blocks = [stem]
    if kind != "no_options":
        labels = "ACD" if kind == "missing_option" else "ABCD"
        for label in labels:
            value = "内容很长" * 40 if kind == "long_options" else "0.5"
            block = {"text": f"{label}. {value}", "html": f"<p>{label}. {value}</p>"}
            if kind == "formula_options":
                block.update(
                    text=f"{label}.",
                    html=f'<p>{label}.<img src="media/synthetic-{label}.png" width="120" height="25"/></p>',
                    media_occurrences=[{"kind": "ole_preview", "ole_id": f"synthetic-{label}"}],
                    ole_occurrences=[
                        {
                            "ole_id": f"synthetic-{label}",
                            "source_part": f"word/embeddings/{label}.bin",
                        }
                    ],
                )
            blocks.append(block)
    original = deepcopy(blocks)
    units = _question_units(blocks, "single_choice")
    flattened = [
        block for mode, value in units for block in (value if mode == "options" else [value])
    ]
    assert flattened == original
    assert blocks == original
    if kind in {"no_options", "missing_option"}:
        assert all(mode == "block" for mode, _ in units), (
            "ambiguous options must preserve source layout"
        )


def test_inline_function_arguments_are_not_counted_as_three_subquestions():
    from app.source_exporting import _subquestion_count

    assert _subquestion_count([{"text": "若 f(1)=2、f(2)=3，求 f(3)。"}]) == 1
    assert (
        _subquestion_count(
            [{"text": "（1）求值。"}, {"text": "（2）证明。"}, {"text": "（3）求长度。"}]
        )
        == 3
    )


def test_image_first_stem_keeps_number_and_proportional_picture_in_same_paragraph(tmp_path):
    import hashlib
    from copy import deepcopy

    from app.print_layout import make_exam_document
    from app.source_exporting import _DocumentRenderer
    from PIL import Image

    relative = "media/synthetic.png"
    path = tmp_path / relative
    path.parent.mkdir()
    Image.new("RGB", (40, 20), "white").save(path)
    block = {
        "document_id": "synthetic-document",
        "paragraph_index": 1,
        "text": "",
        "html": f'<p><img src="{relative}" width="40" height="20"/></p>',
        "media_occurrences": [
            {"media_path": relative, "kind": "drawingml", "rendered": True, "occurrence": 1}
        ],
        "ole_occurrences": [],
        "omml_occurrences": [],
    }
    original = deepcopy(block)
    document = make_exam_document(teacher=False, layout="compact", is_synthetic_student=True)
    renderer = _DocumentRenderer(document, tmp_path, [0], [])
    renderer.render_block(
        block,
        assets={relative: hashlib.sha256(path.read_bytes()).hexdigest()},
        question_number=1,
        remove_source_number=True,
        source_number="17",
    )
    paragraph = document.paragraphs[-1]
    assert paragraph.text.lstrip().startswith(("1.", "1．"))
    assert paragraph._p.xpath(".//w:drawing")
    picture = document.inline_shapes[-1]
    assert picture.width == picture.height * 2
    assert block == original


def synthetic_export_inputs(tmp_path):
    """Text-only synthetic source; the fake conversion seam proves no PDF layout."""
    import hashlib
    import json

    from app.bank_practice import build_bank_practice
    from app.bank_practice_contracts import BankQuestion, PracticeBank
    from app.contracts import Curriculum, PracticeRequest, Quota, StudentProgress
    from app.semantic_bank import canonical_hash

    curriculum = Curriculum.model_validate(
        {"version": "synthetic-layout-v1", "points": [{"id": "p", "name": "合成考点"}]}
    )
    source = {
        "id": "synthetic-layout-question",
        "number": 17,
        "question_type": "free_response",
        "structure_status": "imported",
        "association_status": "confirmed",
        "answer_available": True,
    }

    def block(index, text):
        return {
            "document_id": "synthetic-document",
            "paragraph_index": index,
            "text": text,
            "html": f"<p>{text}</p>",
            "media_occurrences": [],
            "omml_occurrences": [],
            "ole_occurrences": [],
        }

    source["stem_blocks"] = [
        block(1, "17. SYNTHETIC STEM 1.5 是小数"),
        block(2, "（1）保留小问 0.5x=1"),
    ]
    source["answer_blocks"] = [block(3, "【答案】SYNTHETIC ANSWER 2")]
    source["analysis_blocks"] = [
        block(4, "【解析】SYNTHETIC SOLUTION"),
        block(5, "数学行内标签【答案】及解析必须保留，解0.5x=1。"),
    ]
    semantic = {
        "status": "assessed",
        "assessment": {
            "question_id": source["id"],
            "status": "assessed",
            "point_ids": ["p"],
            "prerequisite_ids": [],
            "difficulty_score": 50,
            "reasoning": "Synthetic layout boundary only.",
            "warnings": [],
            "point_evidence": [{"point_id": "p", "role": "target", "reason": "Synthetic."}],
        },
    }
    question = BankQuestion(
        id=source["id"],
        revision="synthetic-revision",
        canonical_id=source["id"],
        content_fingerprint="synthetic-layout-fingerprint",
        curriculum_version=curriculum.version,
        difficulty_version="junior-math-demand-v1",
        point_ids=["p"],
        prerequisite_ids=[],
        difficulty_score=50,
        status="eligible",
        block_reasons=[],
        source_hash=canonical_hash(source),
        semantic_hash=canonical_hash(semantic),
        source_payload=source,
        semantic_payload=semantic,
        documents=[],
        asset_hashes={},
    )
    bank_dir = tmp_path / "bank"
    bank_dir.mkdir()
    raw = json.dumps({"questions": [source], "documents": []}, ensure_ascii=False).encode()
    (bank_dir / "bank.json").write_bytes(raw)
    bank = PracticeBank(
        source_bank_sha256=hashlib.sha256(raw).hexdigest(),
        annotations_sha256="a" * 64,
        curriculum_sha256=canonical_hash(curriculum.model_dump(mode="json")),
        curriculum_version=curriculum.version,
        curriculum_snapshot=curriculum,
        questions=[question],
    )
    progress = StudentProgress(
        student_id="synthetic-layout-student",
        revision="synthetic-r1",
        curriculum_version=curriculum.version,
        learned_point_ids=["p"],
    )
    request = PracticeRequest(
        quotas=[Quota(point_id="p", count=1)], difficulty_version="junior-math-demand-v1"
    )
    snapshot = build_bank_practice(bank, curriculum, progress, request, is_synthetic_student=True)
    return bank_dir, bank, curriculum, progress, snapshot


def test_short_question_keep_chain_stops_before_the_next_question(tmp_path):
    from copy import deepcopy
    from types import SimpleNamespace

    from app.source_exporting import _document_for_item

    _, bank, curriculum, _, _ = synthetic_export_inputs(tmp_path)
    source = deepcopy(bank.questions[0].source_payload)
    source["question_type"] = "fill_blank"
    item = SimpleNamespace(
        snapshot_is_synthetic=True,
        snapshot_items=[
            SimpleNamespace(
                order=1, target_point_id="p", question=SimpleNamespace(source_payload=source)
            )
        ],
    )
    document, _, _ = _document_for_item(
        item,
        teacher=False,
        layout="compact",
        curriculum=curriculum,
        bank_root=tmp_path,
        marker_counter=[0],
        marker_metadata=[],
        assets={},
    )
    last_stem = next(p for p in document.paragraphs if "（1）保留小问" in p.text)
    assert last_stem.paragraph_format.keep_with_next is False
    keep_lines = last_stem.paragraph_format.keep_together
    style = last_stem.style
    while keep_lines is None and style is not None:
        keep_lines = style.paragraph_format.keep_together
        style = style.base_style
    assert keep_lines is True, "the last short prompt must not fragment across pages"


def test_short_table_links_all_rows_to_whole_prompt_and_stops_before_next_question():
    from app.source_exporting import _keep_short_question_within_its_stem
    from docx import Document

    document = Document()
    intro = document.add_paragraph("3. Synthetic table question")
    table = document.add_table(rows=3, cols=8)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell.text = str(index)
    prompt = document.add_paragraph(
        "Synthetic complete prompt with a final decimal precision clause."
    )
    next_question = document.add_paragraph("4. A separate question")
    _keep_short_question_within_its_stem([intro._p, table._tbl, prompt._p])
    assert intro.paragraph_format.keep_with_next is True
    for row in table.rows:
        assert row._tr.xpath("./w:trPr/w:cantSplit")
        for cell in row.cells:
            assert all(p.paragraph_format.keep_with_next is True for p in cell.paragraphs)
    assert prompt.paragraph_format.keep_together is True
    assert prompt.paragraph_format.keep_with_next is False
    assert next_question.paragraph_format.keep_with_next is None


@pytest.mark.parametrize("layout", ["answer_space", "compact"])
def test_teacher_scope_and_label_cleaning_preserve_math_and_snapshot(tmp_path, monkeypatch, layout):
    import json
    import sys
    from copy import deepcopy
    from pathlib import Path

    from app import source_exporting
    from docx import Document

    bank_dir, bank, curriculum, progress, snapshot = synthetic_export_inputs(tmp_path)
    original = deepcopy(snapshot.model_dump())

    def fake_pdf(docx, output, soffice, profile):
        pdf = Path(output) / f"{Path(docx).stem}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nsynthetic conversion seam only\n")
        return pdf

    monkeypatch.setattr(source_exporting, "_convert_pdf", fake_pdf)
    result = source_exporting.export_bank_snapshot(
        snapshot,
        tmp_path / f"out-{layout}",
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        soffice_executable=sys.executable,
        layout=layout,
    )
    teacher = Document(result["files"]["teacher_docx"])
    student = Document(result["files"]["student_docx"])
    teacher_text = "\n".join(p.text for p in teacher.paragraphs)
    student_text = "\n".join(p.text for p in student.paragraphs)
    assert "SYNTHETIC STEM" not in teacher_text and "保留小问" not in teacher_text
    assert "SYNTHETIC ANSWER 2" in teacher_text and "SYNTHETIC SOLUTION" in teacher_text
    first_answer = next(p for p in teacher.paragraphs if "SYNTHETIC ANSWER 2" in p.text)
    assert first_answer.text.lstrip().startswith(("1.", "1．", "1、", "1)")), (
        "a short teacher answer belongs on the question-number paragraph"
    )
    assert "数学行内标签【答案】及解析必须保留，解0.5x=1。" in teacher_text
    assert not any(p.text.lstrip().startswith(("【答案】", "【解析】")) for p in teacher.paragraphs)
    assert "1.5 是小数" in student_text and "（1）保留小问 0.5x=1" in student_text
    assert "17." not in student_text
    first = next(p for p in student.paragraphs if "SYNTHETIC STEM" in p.text)
    assert first.text.lstrip().startswith(("1.", "1．", "1、", "1)")), (
        "question label and first stem must be one paragraph"
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["layout"] == layout and manifest["template_version"] == "exam-print-v1"
    assert manifest["teacher_content"] == "answers_and_solutions"
    assert snapshot.model_dump() == original


def display_formula_xml(*, intro="1.\t计算：", token_number=1):
    """Synthetic OOXML only; actual CFB/preview bytes are checked against real source ZIPs."""
    from lxml import etree

    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "v": "urn:schemas-microsoft-com:vml",
        "o": "urn:schemas-microsoft-com:office:office",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    }
    body = etree.Element(qn("w:body"), nsmap=ns)
    paragraph = etree.SubElement(body, qn("w:p"))
    intro_run = etree.SubElement(paragraph, qn("w:r"))
    etree.SubElement(intro_run, qn("w:t")).text = intro
    placeholder = etree.SubElement(paragraph, qn("w:r"))
    etree.SubElement(placeholder, qn("w:t")).text = f"TSXMARKER_OLE_{token_number:08d}"
    output_run = etree.Element(qn("w:r"), nsmap=ns)
    obj = etree.SubElement(output_run, qn("w:object"))
    shape_id = f"synthetic-shape-{token_number}"
    shape = etree.SubElement(
        obj, f"{{{ns['v']}}}shape", id=shape_id, style="width:80pt;height:30pt"
    )
    etree.SubElement(shape, f"{{{ns['v']}}}imagedata", {qn("r:id"): f"rIdPreview{token_number}"})
    etree.SubElement(
        obj,
        f"{{{ns['o']}}}OLEObject",
        {"ProgID": "Equation.DSMT4", "ShapeID": shape_id, qn("r:id"): f"rIdNative{token_number}"},
    )
    return body, paragraph, placeholder, output_run, obj


def xml_identity(node):
    return (
        node.tag,
        tuple(sorted(node.attrib.items())),
        node.text,
        node.tail,
        tuple(xml_identity(c) for c in node),
    )


def test_single_large_calculation_can_move_to_display_without_changing_object_or_order():
    from app.source_exporting import _separate_large_display_formula
    from lxml import etree

    body, paragraph, placeholder, output, obj = display_formula_xml()
    original = xml_identity(obj)
    next_question = etree.SubElement(body, qn("w:p"))
    assert _separate_large_display_formula(paragraph, placeholder, output, obj) is True
    display = paragraph.getnext()
    assert display is not None and display.getnext() is next_question
    assert obj.getparent() is output and output.getparent() is display
    assert xml_identity(obj) == original
    assert body.xpath(".//w:object", namespaces=body.nsmap) == [obj]
    assert "计算：" in "".join(paragraph.itertext())


@pytest.mark.parametrize("existing", ["narration", "drawing", "omml"])
def test_display_reflow_rejects_narration_or_existing_visible_media(existing):
    from app.source_exporting import _separate_large_display_formula
    from lxml import etree

    body, paragraph, placeholder, output, obj = display_formula_xml(
        intro="若函数满足下列关系：" if existing == "narration" else "1.\t计算："
    )
    if existing == "drawing":
        run = etree.Element(qn("w:r"))
        etree.SubElement(run, qn("w:drawing"))
        paragraph.insert(1, run)
    elif existing == "omml":
        math = etree.Element("{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
        paragraph.insert(1, math)
    original = xml_identity(body)
    assert _separate_large_display_formula(paragraph, placeholder, output, obj) is False
    assert xml_identity(body) == original


def test_two_formula_replacement_does_not_turn_second_object_into_single_formula_case():
    from app.source_exporting import _separate_large_display_formula

    body, paragraph, first, first_output, first_object = display_formula_xml(token_number=1)
    _, _, second, second_output, second_object = display_formula_xml(token_number=2)
    paragraph.append(second)
    originals = [xml_identity(first_object), xml_identity(second_object)]
    assert _separate_large_display_formula(paragraph, first, first_output, first_object) is False
    paragraph.replace(first, first_output)  # Same sequential replacement as the real remapper.
    assert _separate_large_display_formula(paragraph, second, second_output, second_object) is False
    paragraph.replace(second, second_output)
    assert len(body) == 1
    assert [
        xml_identity(node) for node in body.xpath(".//w:object", namespaces=body.nsmap)
    ] == originals
