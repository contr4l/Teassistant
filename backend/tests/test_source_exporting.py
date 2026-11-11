from __future__ import annotations

import copy
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from app.bank_practice import build_bank_practice
from app.bank_practice_contracts import BankQuestion, PracticeBank, SourceDocument
from app.contracts import Curriculum, KnowledgePoint, PracticeRequest, Quota, StudentProgress
from app.exporting import ExportError
from app.semantic_bank import canonical_hash
from app.semantic_contracts import DIFFICULTY_VERSION
from app.source_exporting import (
    _DocumentRenderer,
    _inline_option_columns,
    _inline_option_labels,
    _keep_short_question_within_its_stem,
    _option_columns,
    _question_units,
    _safe_html,
    _separate_large_display_formula,
    _strip_source_number,
    _validate_source_bank,
    export_bank_snapshot,
)
from docx import Document
from lxml import etree
from PIL import Image

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{W}}}p"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_fixture(
    tmp_path: Path, *, image: bool = False, ole: bool = False, display_formula: bool = False
):
    bank_dir = tmp_path / "bank"
    (bank_dir / "sources").mkdir(parents=True)
    media_dir = bank_dir / "media" / "doc1"
    media_dir.mkdir(parents=True)
    source_docx = bank_dir / "sources" / "source.docx"
    source_doc = Document()
    source_doc.add_paragraph("source document fixture")
    source_doc.save(source_docx)
    curriculum = Curriculum(
        version="curriculum-test-v1",
        points=[KnowledgePoint(id="p1", name="分数运算")],
    )
    asset_hashes = {}
    media_path = "media/doc1/picture.png"
    media_occurrences = []
    html_fragment = (
        '<p>1. 保留小数0.5与公式<math xmlns="http://www.w3.org/1998/Math/MathML">'
        "<mi>x</mi></math></p>"
        "<table><tbody><tr><td>甲</td><td>乙</td></tr><tr><td>1</td><td>2</td></tr></tbody></table>"
    )
    ole_bytes = b"synthetic-native-ole-object"
    ole_occurrences = []
    if image or ole:
        image_file = media_dir / "picture.png"
        Image.new("RGB", (20, 10), "white").save(image_file)
        asset_hashes[media_path] = _hash(image_file.read_bytes())
        intro = "计算：" if display_formula else "图形"
        suffix = "。" if display_formula else ""
        html_fragment = f'<p>1. {intro}<img src="{media_path}" width="20" height="10"/>{suffix}</p>'
        media_occurrences = [
            {
                "occurrence": 1,
                "paragraph_index": 0,
                "source_rel_id": "rIdOlePreview" if ole else "rId8",
                "source_part": "word/media/ole-preview.png"
                if ole
                else "word/media/source-picture.png",
                "kind": "ole_preview" if ole else "drawingml",
                "ole_id": "_x0000_i777" if ole else None,
                "media_path": media_path,
                "rendered": True,
            }
        ]
        if ole:
            ole_occurrences = [
                {
                    "ole_id": "_x0000_i777",
                    "ole_rel_id": "rIdOleBin",
                    "preview_rel_ids": ["rIdOlePreview"],
                    "preview_media_paths": [media_path],
                    "native_equation": {
                        "status": "valid",
                        "method": "native_mtef_exact_v1",
                        "payload_sha256": "c" * 64,
                        "payload_size": 10,
                        "mtef_version": 5,
                        "ole_sha256": _hash(ole_bytes),
                    },
                }
            ]
            _inject_ole_package(source_docx, image_file.read_bytes(), ole_bytes)
        else:
            _inject_image_package(source_docx, image_file.read_bytes())
    omml = f'<m:oMath xmlns:m="{M}"><m:r><m:t>x</m:t></m:r></m:oMath>'
    stem_block = {
        "document_id": "doc1",
        "paragraph_index": 0,
        "source_text_range": [0, 20],
        "segment_index": 0,
        "text": "1. 计算：x" if display_formula else "1. 保留小数0.5与公式x",
        "html": html_fragment,
        "media_occurrences": media_occurrences,
        "ole_occurrences": ole_occurrences,
        "omml_occurrences": []
        if image or ole
        else [{"occurrence": 1, "paragraph_index": 0, "source_xml": omml, "supported": True}],
    }
    if not image:
        stem_block["html"] = html_fragment
    answer_block = {
        "document_id": "doc1",
        "paragraph_index": 1,
        "source_text_range": [0, 2],
        "segment_index": 1,
        "text": "42",
        "html": "<p>42</p>",
        "media_occurrences": [],
        "ole_occurrences": [],
        "omml_occurrences": [],
    }
    payload = {
        "id": "q1",
        "number": 1,
        "question_type": "single_choice",
        "structure_status": "imported",
        "association_status": "confirmed",
        "answer_available": True,
        "stem_blocks": [stem_block],
        "answer_blocks": [answer_block],
        "analysis_blocks": [],
    }
    semantic = {
        "status": "assessed",
        "assessment": {
            "question_id": "q1",
            "status": "assessed",
            "point_ids": ["p1"],
            "prerequisite_ids": [],
            "difficulty_score": 20,
            "reasoning": "单步基础运算。",
            "point_evidence": [
                {"point_id": "p1", "role": "target", "reason": "题目要求分数运算。"}
            ],
            "warnings": [],
        },
    }
    question = BankQuestion(
        id="q1",
        revision="rev-q1",
        canonical_id="canonical-q1",
        content_fingerprint="fingerprint-q1",
        curriculum_version=curriculum.version,
        difficulty_version=DIFFICULTY_VERSION,
        point_ids=["p1"],
        prerequisite_ids=[],
        difficulty_score=20,
        status="eligible",
        block_reasons=[],
        source_hash=canonical_hash(payload),
        semantic_hash=canonical_hash(semantic),
        source_payload=payload,
        semantic_payload=semantic,
        documents=[
            SourceDocument(
                id="doc1", path="sources/source.docx", sha256=_hash(source_docx.read_bytes())
            )
        ],
        asset_hashes=asset_hashes,
    )
    source_doc_sha = _hash(source_docx.read_bytes())
    raw_bank = json.dumps(
        {
            "questions": [payload],
            "documents": [
                {
                    "id": "doc1",
                    "sha256": source_doc_sha,
                    "source_copies": [{"path": "sources/source.docx", "sha256": source_doc_sha}],
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    (bank_dir / "bank.json").write_bytes(raw_bank)
    bank = PracticeBank(
        source_bank_sha256=_hash(raw_bank),
        annotations_sha256="a" * 64,
        curriculum_sha256="b" * 64,
        curriculum_version=curriculum.version,
        curriculum_snapshot=curriculum,
        questions=[question],
    )
    progress = StudentProgress(
        student_id="synthetic-test",
        revision="progress-r1",
        curriculum_version=curriculum.version,
        learned_point_ids=["p1"],
    )
    request = PracticeRequest(
        quotas=[Quota(point_id="p1", count=1, by_difficulty={"basic": 1})],
        difficulty_version=DIFFICULTY_VERSION,
    )
    snapshot = build_bank_practice(bank, curriculum, progress, request, is_synthetic_student=True)
    return bank_dir, bank, curriculum, progress, snapshot, source_docx, media_path


def _fake_soffice(path: Path) -> Path:
    executable = path / "fake-soffice"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import pathlib,sys\n"
        "args=sys.argv[1:]\n"
        "out=pathlib.Path(args[args.index('--outdir')+1])\n"
        "docx=pathlib.Path(args[-1])\n"
        "(out/(docx.stem+'.pdf')).write_bytes(b'%PDF-1.4\\nfixture\\n')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _inject_ole_package(source_docx: Path, preview_bytes: bytes, ole_bytes: bytes) -> None:
    with zipfile.ZipFile(source_docx) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(files["word/document.xml"])
    paragraph = root.find(f".//{{{W}}}body/{{{W}}}p")
    assert paragraph is not None
    run = etree.SubElement(paragraph, f"{{{W}}}r")
    obj = etree.SubElement(run, f"{{{W}}}object")
    pict = etree.SubElement(obj, f"{{{W}}}pict")
    shape = etree.SubElement(
        pict,
        f"{{{V}}}shape",
        id="_x0000_i777",
        type="#_x0000_t75",
        style="width:50pt;height:30pt",
    )
    shape.set(f"{{{OFFICE}}}spt", "75")
    etree.SubElement(shape, f"{{{V}}}imagedata", **{f"{{{R}}}id": "rIdOlePreview"})
    etree.SubElement(
        obj,
        f"{{{OFFICE}}}OLEObject",
        Type="Embed",
        ProgID="Equation.DSMT4",
        ShapeID="_x0000_i777",
        ObjectID="_{{00000000-0000-0000-0000-000000000001}}",
        **{f"{{{R}}}id": "rIdOleBin"},
    )
    files["word/document.xml"] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    rel_path = "word/_rels/document.xml.rels"
    rels = etree.fromstring(files[rel_path])
    etree.SubElement(
        rels,
        f"{{{PKG}}}Relationship",
        Id="rIdOlePreview",
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        Target="media/ole-preview.png",
    )
    etree.SubElement(
        rels,
        f"{{{PKG}}}Relationship",
        Id="rIdOleBin",
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject",
        Target="embeddings/oleObject1.bin",
    )
    files[rel_path] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    files["word/media/ole-preview.png"] = preview_bytes
    files["word/embeddings/oleObject1.bin"] = ole_bytes
    source_types = etree.fromstring(files["[Content_Types].xml"])
    etree.SubElement(
        source_types,
        f"{{{CT}}}Override",
        PartName="/word/embeddings/oleObject1.bin",
        ContentType="application/vnd.openxmlformats-officedocument.oleObject",
    )
    files["[Content_Types].xml"] = etree.tostring(
        source_types, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    with zipfile.ZipFile(source_docx, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def _inject_image_package(source_docx: Path, image_bytes: bytes) -> None:
    with zipfile.ZipFile(source_docx) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    rel_path = "word/_rels/document.xml.rels"
    rels = etree.fromstring(files[rel_path])
    etree.SubElement(
        rels,
        f"{{{PKG}}}Relationship",
        Id="rId8",
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        Target="media/source-picture.png",
    )
    files[rel_path] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    files["word/media/source-picture.png"] = image_bytes
    with zipfile.ZipFile(source_docx, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def test_source_number_removal_matches_only_explicit_item_label():
    assert _strip_source_number("1. 0.5 remains", "1") == "0.5 remains"
    assert _strip_source_number("0.5 is decimal", "1") == "0.5 is decimal"
    assert _strip_source_number("10. a question", "1") == "10. a question"
    assert _strip_source_number("1.5 is a decimal", "1") == "1.5 is a decimal"
    assert _strip_source_number("1. a question", None) == "1. a question"


def test_safe_html_rejects_scripts_remote_images_and_unknown_attrs():
    with pytest.raises(ExportError):
        _safe_html("<script>alert(1)</script>")
    with pytest.raises(ExportError):
        _safe_html('<img src="https://example.invalid/a.png">')
    with pytest.raises(ExportError):
        _safe_html('<p onclick="run()">text</p>')


def test_transactional_export_keeps_native_omml_table_and_student_answer_separation(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _make_fixture(tmp_path)
    output = tmp_path / "exports"
    result = export_bank_snapshot(
        snapshot,
        output,
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        soffice_executable=_fake_soffice(tmp_path),
    )
    assert set(result["files"]) == {"student_docx", "student_pdf", "teacher_docx", "teacher_pdf"}
    assert all(Path(value).is_file() for value in result["files"].values())
    assert (output / "manifest.json").is_file()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["progress_validation"] == "passed"
    assert manifest["is_synthetic_student"] is True
    assert manifest["files"]["student_docx"]["path"] == "student.docx"
    assert manifest["counts"]["student"]["omml_native_count"] == 1
    with zipfile.ZipFile(result["files"]["student_docx"]) as archive:
        xml = etree.fromstring(archive.read("word/document.xml"))
        assert xml.find(f".//{{{M}}}oMath") is not None
        assert b"TSXMARKER" not in archive.read("word/document.xml")
    student_doc = Document(result["files"]["student_docx"])
    teacher_doc = Document(result["files"]["teacher_docx"])
    student_text = "\n".join(p.text for p in student_doc.paragraphs)
    teacher_text = "\n".join(p.text for p in teacher_doc.paragraphs)
    assert "合成学生进度演示 · 非真实学生" in student_text
    assert "0.5" in student_text
    first_stem = next(p for p in student_doc.paragraphs if "0.5" in p.text)
    assert first_stem.paragraph_format.keep_with_next is True
    assert "答案：" not in student_text
    assert "答案：" in teacher_text and "42" in teacher_text
    assert "0.5" not in teacher_text
    assert manifest["layout"] == "answer_space"
    assert manifest["template_version"] == "exam-print-v1"
    assert manifest["teacher_content"] == "answers_and_solutions"
    assert len(student_doc.tables) == 1
    assert [cell.text for cell in student_doc.tables[0].rows[0].cells] == ["甲", "乙"]
    assert len(teacher_doc.tables) == 0
    assert (output / "student.pdf").read_bytes().startswith(b"%PDF-")


def test_image_export_checks_bytes_and_preserves_aspect_ratio(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _make_fixture(tmp_path, image=True)
    output = tmp_path / "image-export"
    export_bank_snapshot(
        snapshot,
        output,
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        soffice_executable=_fake_soffice(tmp_path),
    )
    doc = Document(output / "student.docx")
    images = [rel for rel in doc.part.rels.values() if rel.reltype.endswith("/image")]
    assert len(images) == 1
    with zipfile.ZipFile(output / "student.docx") as archive:
        xml = etree.fromstring(archive.read("word/document.xml"))
        extent = xml.find(f".//{{{WP}}}extent")
        assert extent is not None
        assert int(extent.get("cx")) == 2 * int(extent.get("cy"))


def test_native_ole_is_reembedded_with_bound_preview_and_unique_ids(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, source_docx, _ = _make_fixture(
        tmp_path, ole=True
    )
    original_source_sha = _hash(source_docx.read_bytes())
    output = tmp_path / "ole-export"
    result = export_bank_snapshot(
        snapshot,
        output,
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        soffice_executable=_fake_soffice(tmp_path),
    )
    assert _hash(source_docx.read_bytes()) == original_source_sha
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"]["student"]["native_ole_count"] == 1
    with zipfile.ZipFile(result["files"]["student_docx"]) as archive:
        document_xml = etree.fromstring(archive.read("word/document.xml"))
        object_node = document_xml.find(f".//{{{W}}}r/{{{W}}}object")
        assert object_node is not None
        shape = object_node.find(f".//{{{V}}}shape")
        ole = object_node.find(f".//{{{OFFICE}}}OLEObject")
        assert shape is not None and ole is not None
        assert shape.get("id") == ole.get("ShapeID")
        assert shape.get("id") != "_x0000_i777"
        assert (
            ole.get("ObjectID") and ole.get("ObjectID") != "_{00000000-0000-0000-0000-000000000001}"
        )
        rels = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        rel_by_id = {rel.get("Id"): rel for rel in rels}
        ole_rel = rel_by_id[ole.get(f"{{{R}}}id")]
        image_rel = rel_by_id[shape.find(f".//{{{V}}}imagedata").get(f"{{{R}}}id")]
        ole_part = "word/" + ole_rel.get("Target")
        image_part = "word/" + image_rel.get("Target")
        object_paragraph = object_node.getparent().getparent()
        assert "图形" in "".join(object_paragraph.itertext())
        assert archive.read(ole_part) == b"synthetic-native-ole-object"
        assert archive.read(image_part) == (bank_dir / "media/doc1/picture.png").read_bytes()
        assert "[Content_Types].xml" in archive.namelist()


def test_large_math_type_after_short_calculation_intro_gets_display_paragraph(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _make_fixture(
        tmp_path, ole=True, display_formula=True
    )
    output = tmp_path / "display-formula-export"
    result = export_bank_snapshot(
        snapshot,
        output,
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        soffice_executable=_fake_soffice(tmp_path),
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"]["student"]["source_ref_count"] == 2
    with zipfile.ZipFile(result["files"]["student_docx"]) as archive:
        document_xml = etree.fromstring(archive.read("word/document.xml"))
        intro_paragraph = next(
            p for p in document_xml.iter(W_P) if "计算：" in "".join(p.itertext())
        )
        display_paragraph = intro_paragraph.getnext()
        assert display_paragraph is not None and display_paragraph.tag == W_P
        object_node = display_paragraph.find(f".//{{{W}}}object")
        assert object_node is not None
        assert intro_paragraph.find(f"./{{{W}}}pPr/{{{W}}}keepNext") is not None
        display_keep = display_paragraph.find(f"./{{{W}}}pPr/{{{W}}}keepNext")
        assert display_keep is not None and display_keep.get(f"{{{W}}}val") == "0"
        assert (
            display_paragraph.find(f"./{{{W}}}pPr/{{{W}}}pStyle").get(f"{{{W}}}val") == "ExamBody"
        )
        rels = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        rel_by_id = {rel.get("Id"): rel for rel in rels}
        ole = object_node.find(f".//{{{OFFICE}}}OLEObject")
        ole_part = "word/" + rel_by_id[ole.get(f"{{{R}}}id")].get("Target")
        assert archive.read(ole_part) == b"synthetic-native-ole-object"


def test_display_formula_guard_rejects_multiple_markers_and_trailing_picture():
    paragraph = etree.Element(W_P)
    intro_run = etree.SubElement(paragraph, f"{{{W}}}r")
    etree.SubElement(intro_run, f"{{{W}}}t").text = "1.\t计算："
    first_placeholder = etree.SubElement(paragraph, f"{{{W}}}r")
    etree.SubElement(first_placeholder, f"{{{W}}}t").text = "TSXMARKER_OLE_00000001"
    second_placeholder = etree.SubElement(paragraph, f"{{{W}}}r")
    etree.SubElement(second_placeholder, f"{{{W}}}t").text = "TSXMARKER_OLE_00000002"

    def make_large_output_run():
        output_run = etree.Element(f"{{{W}}}r")
        object_element = etree.SubElement(output_run, f"{{{W}}}object")
        shape = etree.SubElement(
            object_element,
            f"{{{V}}}shape",
            id="shape1",
            style="width:50pt;height:30pt",
        )
        etree.SubElement(
            shape,
            f"{{{OFFICE}}}OLEObject",
            ProgID="Equation.DSMT4",
            ShapeID="shape1",
        )
        return output_run, object_element

    first_output, first_object = make_large_output_run()
    assert not _separate_large_display_formula(
        paragraph, first_placeholder, first_output, first_object
    )
    paragraph.remove(first_placeholder)
    paragraph.insert(1, first_output)
    second_output, second_object = make_large_output_run()
    assert not _separate_large_display_formula(
        paragraph, second_placeholder, second_output, second_object
    )

    single_marker_paragraph = etree.Element(W_P)
    single_intro = etree.SubElement(single_marker_paragraph, f"{{{W}}}r")
    etree.SubElement(single_intro, f"{{{W}}}t").text = "计算："
    marker_run = etree.SubElement(single_marker_paragraph, f"{{{W}}}r")
    etree.SubElement(marker_run, f"{{{W}}}t").text = "TSXMARKER_OLE_00000003"
    picture_tail = etree.SubElement(single_marker_paragraph, f"{{{W}}}r")
    etree.SubElement(picture_tail, f"{{{W}}}drawing")
    etree.SubElement(picture_tail, f"{{{W}}}t").text = "。"
    third_output, third_object = make_large_output_run()
    assert not _separate_large_display_formula(
        single_marker_paragraph, marker_run, third_output, third_object
    )


def test_short_question_keeps_paragraph_lines_and_table_chain_together():
    intro = etree.Element(W_P)
    table = etree.Element(f"{{{W}}}tbl")
    for _ in range(3):
        row = etree.SubElement(table, f"{{{W}}}tr")
        for _ in range(8):
            cell = etree.SubElement(row, f"{{{W}}}tc")
            etree.SubElement(cell, W_P)
    caption = etree.Element(W_P)
    following_question = etree.Element(W_P)

    _keep_short_question_within_its_stem([intro, table, caption])

    for paragraph in [intro, *table.iter(W_P), caption]:
        assert paragraph.find(f"./{{{W}}}pPr/{{{W}}}keepLines") is not None
    table_paragraphs = list(table.iter(W_P))
    assert all(
        paragraph.find(f"./{{{W}}}pPr/{{{W}}}keepNext") is not None
        and paragraph.find(f"./{{{W}}}pPr/{{{W}}}keepNext").get(f"{{{W}}}val") != "0"
        for paragraph in table_paragraphs
    )
    assert all(row.find(f"./{{{W}}}trPr/{{{W}}}cantSplit") is not None for row in table)
    caption_keep = caption.find(f"./{{{W}}}pPr/{{{W}}}keepNext")
    assert caption_keep is not None and caption_keep.get(f"{{{W}}}val") == "0"
    assert following_question.find(f"./{{{W}}}pPr") is None


def test_bank_source_payload_cannot_be_rewritten_and_rehashed(tmp_path):
    bank_dir, bank, curriculum, _, _, _, _ = _make_fixture(tmp_path)
    question = bank.questions[0]
    payload = copy.deepcopy(question.source_payload)
    payload["stem_blocks"][0]["text"] = "tampered source text"
    forged_data = question.model_dump(mode="python")
    forged_data["source_payload"] = payload
    forged_data["source_hash"] = canonical_hash(payload)
    forged_data["revision"] = canonical_hash(
        {
            "source_hash": forged_data["source_hash"],
            "semantic_hash": forged_data["semantic_hash"],
        }
    )
    forged_question = BankQuestion.model_validate(forged_data)
    bank_data = bank.model_dump(mode="python")
    bank_data["questions"] = [forged_question]
    forged_bank = PracticeBank.model_validate(bank_data)
    with pytest.raises(ExportError, match="frozen raw bank row"):
        _validate_source_bank(bank_dir, forged_bank, curriculum)


def test_missing_converter_or_progress_mismatch_leaves_no_output(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _make_fixture(tmp_path)
    output = tmp_path / "no-converter"
    with pytest.raises(ExportError):
        export_bank_snapshot(
            snapshot,
            output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress.model_copy(update={"student_id": "other"}),
            soffice_executable="missing-soffice-test-command",
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".no-converter-*"))


def test_pdf_failure_does_not_publish_docx_only_stage(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _make_fixture(tmp_path)
    converter = tmp_path / "no-pdf-soffice"
    converter.write_text("#!" + sys.executable + "\npass\n", encoding="utf-8")
    converter.chmod(0o755)
    output = tmp_path / "failed-conversion"
    with pytest.raises(ExportError, match="did not produce"):
        export_bank_snapshot(
            snapshot,
            output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            soffice_executable=converter,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".failed-conversion-*"))


def test_asset_symlink_escape_is_rejected_before_publication(tmp_path):
    bank_dir, bank, curriculum, progress, snapshot, _, media_path = _make_fixture(
        tmp_path, image=True
    )
    asset = bank_dir / media_path
    outside = tmp_path / "outside.png"
    Image.new("RGB", (20, 10), "white").save(outside)
    asset.unlink()
    asset.symlink_to(outside)
    output = tmp_path / "escaped-asset"
    with pytest.raises(ExportError, match="escapes"):
        export_bank_snapshot(
            snapshot,
            output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            soffice_executable=_fake_soffice(tmp_path),
        )
    assert not output.exists()


def test_inline_formula_choices_are_recognized_and_kept_in_one_paragraph(tmp_path):
    document = Document()
    for style_name in ("ExamBody", "ExamQuestion", "ExamOption"):
        document.styles.add_style(style_name, 1)
    formula = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
    html = "<p>\u00a0\u00a0A. " + formula + "\u00a0\u00a0B. " + formula
    html += "\u00a0\u00a0C. " + formula + "\u00a0\u00a0D. " + formula + "</p>"
    omml = [
        {
            "occurrence": index,
            "paragraph_index": 0,
            "source_xml": f'<m:oMath xmlns:m="{M}"><m:r><m:t>x{index}</m:t></m:r></m:oMath>',
        }
        for index in range(1, 5)
    ]
    block = {
        "document_id": "doc1",
        "paragraph_index": 0,
        "source_text_range": [0, 20],
        "segment_index": 0,
        "text": "\tA. x\tB. x\tC. x\tD. x",
        "html": html,
        "media_occurrences": [],
        "omml_occurrences": omml,
    }
    assert [m.group(1) for m in _inline_option_labels(block)] == list("ABCD")
    assert _question_units([block], "single_choice")[0] == ("inline_options", block)
    assert (
        _option_columns(
            [{"text": "A. abc"}, {"text": "B. abc"}, {"text": "C. abc"}, {"text": "D. abc"}]
        )
        == 4
    )
    renderer = _DocumentRenderer(document, tmp_path, [0], [])
    context = renderer.render_block(block, assets={}, option_columns=4)
    assert context["counts"].get("image_count", 0) == 0
    assert len(document.paragraphs) == 1
    rendered_text = document.paragraphs[0].text
    assert [label for label in "ABCD" if f"{label}. " in rendered_text] == list("ABCD")
    assert rendered_text.count("TSXMARKER_OMML_") == 4
    assert len(document.paragraphs[0]._p.xpath(".//w:r/w:tab")) == 3
    assert len(document.paragraphs[0]._p.xpath(".//w:pPr/w:tabs/w:tab")) == 3
    assert context["used_omml"] == {0, 1, 2, 3}

    oversized_formula_options = {
        "text": "A. B. C. D.",
        "html": (
            "<p>A. <img width='500'/> B. <img width='10'/> "
            "C. <img width='10'/> D. <img width='10'/></p>"
        ),
    }
    assert _inline_option_columns(oversized_formula_options) == 1


WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
