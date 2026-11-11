"""Independent source-bank checks; fixtures contain no real examination content."""

import base64
import json
import sqlite3
import zipfile
from xml.etree import ElementTree as ET

import pytest
from app.bank_index import index_bank, query_bank
from app.bank_view import _safe_fragment


def synthetic_bank():
    return {
        "schema_version": "synthetic-source-bank",
        "documents": [
            {"id": "original", "filename": "synthetic-original.docx", "sha256": "a",
             "role": "original", "pair_id": "pair"},
            {"id": "analysis", "filename": "synthetic-analysis.docx", "sha256": "b",
             "role": "analysis", "pair_id": "pair"},
        ],
        "pairs": [{"id": "pair", "title": "合成资料", "original_document_id": "original",
                   "analysis_document_id": "analysis"}],
        "questions": [
            {"id": f"q{number}", "pair_id": "pair", "number": number,
             "question_type": "synthetic", "section": "合成章节",
             "structure_status": "imported", "practice_ready": False,
             "difficulty_score": None,
             "stem_blocks": [{"text": "合成题干", "html": "合成题干"}],
             "answer_blocks": [{"text": "合成答案", "html": "合成答案"}],
             "analysis_blocks": [{"text": "本题考查二次函数的性质，不涉及相似三角形。",
                                  "html": "本题考查二次函数的性质，不涉及相似三角形。",
                                  "document_id": "analysis", "paragraph_index": number}]}
            for number in [1, 2, 10]
        ],
    }


def test_source_index_preserves_unassessed_status_and_source_evidence(tmp_path):
    source = synthetic_bank()
    result = index_bank(source, tmp_path)
    assert all("topics" not in q for q in source["questions"])
    with sqlite3.connect(tmp_path / "question-bank.sqlite") as connection:
        records = [json.loads(row[0]) for row in connection.execute(
            "SELECT record_json FROM questions")]
    assert len(records) == 3
    for record in records:
        assert record["practice_ready"] is False
        assert record["difficulty_score"] is None
        assert {topic["label"] for topic in record["topics"]} == {"二次函数"}
        assert all(topic["source_document_id"] == "analysis" for topic in record["topics"])
        assert all(topic["source_text"] in record["analysis_blocks"][0]["text"]
                   for topic in record["topics"])
    assert len(result["questions"]) == 3


def test_source_index_returns_numerical_question_order(tmp_path):
    index_bank(synthetic_bank(), tmp_path)
    assert [q["number"] for q in query_bank(tmp_path / "question-bank.sqlite")] == [1, 2, 10]


@pytest.mark.parametrize("src", [
    r"media/..\..\outside.png",
    "media/%2e%2e/%2e%2e/outside.png",
])
def test_browser_fragment_cannot_escape_media_with_browser_url_normalization(src):
    with pytest.raises(ValueError):
        _safe_fragment(f'<img src="{src}">')


def write_synthetic_docx(path, *, analysis=False, missing_preview=False, duplicate=False):
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "v": "urn:schemas-microsoft-com:vml",
        "o": "urn:schemas-microsoft-com:office:office",
    }

    def tag(prefix, name):
        return "{" + namespaces[prefix] + "}" + name

    root = ET.Element(tag("w", "document"))
    body = ET.SubElement(root, tag("w", "body"))

    def paragraph(parent, text):
        p = ET.SubElement(parent, tag("w", "p"))
        ET.SubElement(ET.SubElement(p, tag("w", "r")), tag("w", "t")).text = text
        return p

    def equation(parent, serial):
        obj = ET.SubElement(ET.SubElement(parent, tag("w", "r")), tag("w", "object"))
        shape = ET.SubElement(obj, tag("v", "shape"), {"id": f"shape{serial}"})
        ET.SubElement(shape, tag("v", "imagedata"), {tag("r", "id"): "image"})
        ET.SubElement(obj, tag("o", "OLEObject"), {
            "ShapeID": f"shape{serial}", "ProgID": "Equation.DSMT4", tag("r", "id"): "ole"})

    paragraph(body, "注意事项：")
    paragraph(body, "1．合成考前说明")
    paragraph(body, "2．合成考前说明")
    paragraph(body, "一、选择题")
    first = paragraph(body, "1. 合成第一题")
    equation(first, 1)
    equation(first, 2)  # Two occurrences deliberately reuse the exact same PNG.
    table = ET.SubElement(body, tag("w", "tbl"))
    cell = ET.SubElement(ET.SubElement(table, tag("w", "tr")), tag("w", "tc"))
    equation(paragraph(cell, "合成表格内容"), 3)
    if duplicate:
        paragraph(body, "1. 合成重复题号")
    if analysis:
        paragraph(body, "【答案】合成第一答案")
        paragraph(body, "【分析】本题考查二次函数。")
        paragraph(body, "1. 合成解析内部步骤，保留在解析中")
    paragraph(body, "2. 合成第二题")
    if analysis:
        paragraph(body, "【答案】合成第二答案")
        paragraph(body, "【分析】本题考查概率。")
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rels = ET.Element("{" + rel_ns + "}Relationships")
    for rid, target, kind in [
        ("image", "media/formula.png", "image"),
        ("ole", "embeddings/equation.bin", "oleObject"),
    ]:
        if missing_preview and rid == "image":
            continue
        ET.SubElement(rels, "{" + rel_ns + "}Relationship", {
            "Id": rid, "Target": target,
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/" + kind})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", ET.tostring(root))
        archive.writestr("word/_rels/document.xml.rels", ET.tostring(rels))
        archive.writestr("word/embeddings/equation.bin", b"synthetic-OLE-fixture")
        archive.writestr("word/media/formula.png", base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK9sAAAAASUVORK5CYII="))


def synthetic_sources(tmp_path, *, with_analysis=True, **original_options):
    source = tmp_path / "sources"
    source.mkdir()
    write_synthetic_docx(source / "合成资料（原卷版）.docx", **original_options)
    if with_analysis:
        write_synthetic_docx(source / "精品解析：合成资料（解析版）.docx", analysis=True)
    return source


def test_docx_notice_steps_tables_and_reused_formula_media_are_not_lost(tmp_path):
    from app.docx_ingest import build_source_bank

    bank = build_source_bank(synthetic_sources(tmp_path), tmp_path / "bank")
    assert [q["number"] for q in bank["questions"]] == [1, 2]
    assert all(q["structure_status"] == "imported" for q in bank["questions"])
    first = bank["questions"][0]
    assert first["source_ole_count"] == 3
    assert first["source_image_occurrences"] == first["rendered_image_occurrences"] == 3
    assert sum(block["html"].count("<img ") for block in first["stem_blocks"]) == 3
    assert any("<table>" in block["html"] for block in first["stem_blocks"])
    assert any("合成解析内部步骤" in block["text"] for block in first["analysis_blocks"])
    assert all("合成考前说明" not in block["text"] for block in first["stem_blocks"])


@pytest.mark.parametrize("original_options,with_analysis", [
    ({"missing_preview": True}, True),
    ({"duplicate": True}, True),
    ({}, False),
])
def test_docx_bad_preview_duplicate_or_pair_never_becomes_structurally_usable(
        tmp_path, original_options, with_analysis):
    from app.docx_ingest import build_source_bank

    source = synthetic_sources(tmp_path, with_analysis=with_analysis, **original_options)
    bank = build_source_bank(source, tmp_path / "bank")
    first = next(q for q in bank["questions"] if q["number"] == 1)
    assert first["structure_status"] == "processing_failed"
    assert first["issues"]
    assert first["practice_ready"] is False
    assert first["difficulty_score"] is None
