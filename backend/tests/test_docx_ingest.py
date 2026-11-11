import hashlib
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from app.docx_ingest import DocxIngestError, build_source_bank
from PIL import Image

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"

_png_buffer = io.BytesIO()
Image.new("RGBA", (1, 1), "white").save(_png_buffer, format="PNG")
PNG = _png_buffer.getvalue()
OLE_BYTES = b"synthetic ole payload"


def para(*runs: str) -> str:
    return "<w:p>" + "".join(f"<w:r><w:t>{escape(run)}</w:t></w:r>" for run in runs) + "</w:p>"


def _drawing_and_ole() -> str:
    drawing = (
        '<w:r><w:drawing><wp:inline><wp:extent cx="304800" cy="304800"/>'
        '<wp:graphic><a:graphicData><a:blip r:embed="rImage"/></a:graphicData></wp:graphic>'
        "</wp:inline></w:drawing></w:r>"
    )
    embedded = (
        '<w:r><w:object><v:shape id="shape1" style="width:24pt;height:12pt">'
        '<v:imagedata r:id="rImage"/></v:shape>'
        '<o:OLEObject r:id="rOle" ShapeID="shape1" ProgID="MathType"/>'
        "</w:object></w:r>"
    )
    return drawing + "<w:r><w:tab/></w:r>" + embedded


def make_docx(path: Path, body: str, *, include_rels: bool = True) -> None:
    document = (
        f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" '
        f'xmlns:wp="{WP}" xmlns:v="{V}" xmlns:o="{OFFICE}">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    ).encode()
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
        '<Relationship Id="rOle" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/>'
        "</Relationships>"
    ).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        if include_rels:
            archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/media/image1.png", PNG)
        archive.writestr("word/embeddings/oleObject1.bin", OLE_BYTES)


def fixture_docs(root: Path) -> tuple[Path, Path]:
    source = root / "assets"
    source.mkdir()
    original = source / "Synthetic Exam（原卷版）.docx"
    analysis = source / "精品解析：Synthetic Exam（解析版）.docx"
    original_body = "".join(
        [
            para("注意事项："),
            para("1．考前说明，不是第1题"),
            para("一、选择题"),
            "<w:p><w:r><w:t>1</w:t></w:r><w:r><w:t>. Item &lt;one&gt; &amp; text</w:t></w:r>"
            + _drawing_and_ole()
            + "</w:p>",
            "<w:tbl><w:tblPr/><w:tr><w:tc>" + para("表格证据") + "</w:tc></w:tr></w:tbl>",
            para("2. Second source question"),
            para("二、解答题（含证明题说明）"),
            para("3. Third source question"),
        ]
    )
    analysis_body = "".join(
        [
            para("解析封面数字1．不拆题"),
            para("一、选择题"),
            "<w:p><w:r><w:t>1. Item &lt;one&gt; &amp; text</w:t></w:r>"
            + _drawing_and_ole()
            + "</w:p>",
            "<w:tbl><w:tblPr/><w:tr><w:tc>" + para("表格证据") + "</w:tc></w:tr></w:tbl>",
            para("【答案】A"),
            para("【解析】解题过程"),
            para("1. 详解中的编号不能成为新大题"),
            para("2. Second source question"),
            para("【答案】B"),
            para("【分析】第二题解析"),
            para("二、解答题"),
            para("3. Third source question"),
            para("【答案】42"),
            para("【详解】第三题解析"),
        ]
    )
    make_docx(original, original_body)
    make_docx(analysis, analysis_body)
    return original, analysis


def test_import_preserves_inline_images_ole_tables_and_question_boundaries(tmp_path):
    source, analysis = fixture_docs(tmp_path)
    output = tmp_path / "bank"
    bank = build_source_bank(tmp_path / "assets", output)

    assert bank["schema_version"] == "source-bank-v3"
    assert bank["counts"]["documents"] == 2
    assert bank["counts"]["pairs"] == 1
    assert bank["counts"]["questions"] == 3
    assert bank["counts"]["imported_questions"] == 3
    assert bank["issues"] == []
    assert bank["batch_status"] == "succeeded"
    pair = bank["pairs"][0]
    assert pair["question_count"] == 3
    questions = bank["questions"]
    assert [question["number"] for question in questions] == [1, 2, 3]
    assert questions[0]["question_type"] == "single_choice"
    assert questions[2]["question_type"] == "free_response"
    assert questions[0]["practice_ready"] is False
    assert questions[0]["difficulty_score"] is None
    assert questions[0]["analysis_stem_blocks"][0]["text"].startswith("1.")
    assert any("1. 详解中的编号" in block["text"] for block in questions[0]["analysis_blocks"])
    assert all(
        "1. 详解中的编号" not in block["text"] for block in questions[1]["analysis_stem_blocks"]
    )

    first = questions[0]
    assert first["source_question_range"] == [3, 4]
    assert first["analysis_question_range"][0] == 2
    assert questions[1]["source_question_range"][1] < 6  # section title paragraph is excluded
    stem = "\n".join(block["text"] for block in first["stem_blocks"])
    assert "1. Item <one> & text" in stem
    assert "考前说明" not in stem
    assert any("表格证据" in block["text"] for block in first["stem_blocks"])
    table_block = next(block for block in first["stem_blocks"] if "<table>" in block["html"])
    assert "<table><tbody><tr><td><p>表格证据</p></td></tr></tbody></table>" == table_block["html"]
    assert first["source_image_occurrences"] == 2
    assert first["rendered_image_occurrences"] == 2
    assert first["source_ole_count"] == 1
    inline_block = next(
        block for block in first["stem_blocks"] if block["source_image_occurrences"]
    )
    assert inline_block["media_paths"][0] == inline_block["media_paths"][1]
    assert inline_block["ole_occurrences"][0]["source_part"] == "word/embeddings/oleObject1.bin"
    assert inline_block["ole_occurrences"][0]["preview_media_paths"] == [
        inline_block["media_paths"][1]
    ]
    assert 'width="32" height="32"' in inline_block["html"]  # DrawingML EMU -> CSS px
    assert 'width="32" height="16"' in inline_block["html"]  # VML 24pt x 12pt -> px

    source_doc = next(doc for doc in bank["documents"] if doc["role"] == "original")
    assert source_doc["excluded_leading_paragraph_count"] == 3
    assert source_doc["source_image_occurrences"] == 2
    copied_source = output / "sources" / source.name
    assert hashlib.sha256(copied_source.read_bytes()).hexdigest() == source_doc["sha256"]
    with zipfile.ZipFile(copied_source) as archive:
        assert archive.read("word/embeddings/oleObject1.bin") == OLE_BYTES
        assert (
            archive.read("word/document.xml")
            == (output / "xml" / source_doc["id"] / "document.xml").read_bytes()
        )
        assert (
            archive.read("word/_rels/document.xml.rels")
            == (output / "xml" / source_doc["id"] / "document.xml.rels").read_bytes()
        )
    media = output / inline_block["media_paths"][0]
    assert media.read_bytes() == PNG
    reloaded = json.loads((output / "bank.json").read_text(encoding="utf-8"))
    assert reloaded["counts"] == bank["counts"]
    assert hashlib.sha256(analysis.read_bytes()).hexdigest() == next(
        doc["sha256"] for doc in bank["documents"] if doc["role"] == "analysis"
    )


def test_missing_pair_answer_and_duplicate_markers_are_reported(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    original = assets / "Only（原卷版）.docx"
    make_docx(original, para("注意事项1．ignored") + para("一、选择题") + para("1. Only source"))
    bank = build_source_bank(assets, tmp_path / "output")
    assert bank["counts"]["processing_failed_questions"] == 1
    question = bank["questions"][0]
    assert question["structure_status"] == "processing_failed"
    assert {issue["code"] for issue in question["issues"]} >= {
        "missing_analysis_question",
        "missing_answer",
    }
    assert {issue["code"] for issue in bank["issues"]} >= {"missing_pair_document"}


def test_existing_output_is_never_overwritten(tmp_path):
    source, _ = fixture_docs(tmp_path)
    output = tmp_path / "bank"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("user data", encoding="utf-8")
    with pytest.raises(DocxIngestError, match="already exists"):
        build_source_bank(tmp_path / "assets", output)
    assert sentinel.read_text(encoding="utf-8") == "user data"
    assert source.exists()
