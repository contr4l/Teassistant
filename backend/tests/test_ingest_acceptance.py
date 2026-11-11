"""Independent V2 ingestion acceptance; all documents and images are synthetic."""

import io
import shutil
import sqlite3
import zipfile
from xml.etree import ElementTree as ET

from app.bank_index import index_bank
from app.docx_ingest import build_source_bank
from lxml import html
from PIL import Image, PngImagePlugin

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def paragraph(text):
    node = ET.Element(f"{{{W}}}p")
    append_text(node, text)
    return node


def append_text(node, text):
    ET.SubElement(ET.SubElement(node, f"{{{W}}}r"), f"{{{W}}}t").text = text


def preview(node, rid):
    obj = ET.SubElement(ET.SubElement(node, f"{{{W}}}r"), f"{{{W}}}object")
    shape = ET.SubElement(obj, f"{{{V}}}shape", {"id": rid, "style": "width:12pt;height:12pt"})
    ET.SubElement(shape, f"{{{V}}}imagedata", {f"{{{R}}}id": rid})
    ET.SubElement(
        obj,
        f"{{{OFFICE}}}OLEObject",
        {"ShapeID": rid, "ProgID": "Equation.DSMT4", f"{{{R}}}id": rid + "-ole"},
    )


def image_bytes(color, metadata="synthetic"):
    stream = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("synthetic_fixture", metadata)
    Image.new("RGBA", (2, 2), color).save(stream, format="PNG", pnginfo=info)
    return stream.getvalue()


def write_doc(path, blocks, media=None):
    root = ET.Element(f"{{{W}}}document")
    body = ET.SubElement(root, f"{{{W}}}body")
    for node in blocks:
        body.append(node)
    rels = ET.Element(f"{{{PKG}}}Relationships")
    with zipfile.ZipFile(path, "w") as archive:
        for rid, contents in (media or {}).items():
            for suffix, target, kind in [
                ("", f"media/{rid}.png", "image"),
                ("-ole", f"embeddings/{rid}.bin", "oleObject"),
            ]:
                ET.SubElement(
                    rels,
                    f"{{{PKG}}}Relationship",
                    {
                        "Id": rid + suffix,
                        "Target": target,
                        "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                        + kind,
                    },
                )
            archive.writestr(f"word/media/{rid}.png", contents)
            archive.writestr(f"word/embeddings/{rid}.bin", b"synthetic-OLE-fixture")
        archive.writestr("word/document.xml", ET.tostring(root))
        archive.writestr("word/_rels/document.xml.rels", ET.tostring(rels))


def blocks(numbers=(1,), *, analysis=False, declared=None, explanation=True):
    total = len(numbers) if declared is None else declared
    result = [paragraph(f"一、选择题（共{total}题）")]
    for number in numbers:
        result.append(paragraph(f"{number}. 合成题干{number}"))
        if analysis:
            result.append(paragraph(f"【答案】ANSWER-{number}"))
            if explanation:
                result.append(paragraph(f"【解析】EXPLANATION-{number}"))
    return result


def sources(
    tmp_path,
    original=None,
    analysis=None,
    *,
    analysis_name="精品解析：Synthetic（解析版）.docx",
    original_media=None,
    analysis_media=None,
):
    source = tmp_path / "sources"
    source.mkdir()
    write_doc(
        source / "Synthetic（原卷版）.docx",
        blocks() if original is None else original,
        original_media,
    )
    write_doc(
        source / analysis_name,
        blocks(analysis=True) if analysis is None else analysis,
        analysis_media,
    )
    return source


def questions(bank):
    return {q["number"]: q for q in bank["questions"]}


def test_v2_matching_stems_are_confirmed_without_fabricating_business_readiness(tmp_path):
    bank = build_source_bank(sources(tmp_path), tmp_path / "bank")
    assert bank["schema_version"] == "source-bank-v3"
    assert bank["batch_status"] == "succeeded"
    q = questions(bank)[1]
    assert q["association_status"] == "confirmed"
    assert q["structure_status"] == "imported"
    assert q["answer_available"] is True
    assert q["practice_ready"] is False
    assert q["difficulty_score"] is None


def test_different_stems_cannot_silently_be_confirmed_or_succeed(tmp_path):
    wrong = blocks(analysis=True)
    wrong[1] = paragraph("1. 完全不同的合成问题")
    bank = build_source_bank(sources(tmp_path, analysis=wrong), tmp_path / "bank")
    q = questions(bank)[1]
    assert q["association_status"] == "unverified"
    assert any(i["code"] == "association_unverified" for i in q["issues"])
    assert bank["batch_status"] == "partial"
    assert q["practice_ready"] is False


def test_decimal_punctuation_is_not_erased_from_association_evidence(tmp_path):
    original, analysis = blocks(), blocks(analysis=True)
    original[1] = paragraph("1. 合成条件：长度为1.2米")
    analysis[1] = paragraph("1. 合成条件：长度为12米")
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    q = questions(bank)[1]
    assert q["association_status"] == "unverified"
    assert bank["batch_status"] == "partial"


def test_extra_analysis_question_is_independent_and_never_merges_answers(tmp_path):
    bank = build_source_bank(
        sources(
            tmp_path, original=blocks((1, 3), declared=3), analysis=blocks((1, 2, 3), analysis=True)
        ),
        tmp_path / "bank",
    )
    qs = questions(bank)
    assert set(qs) == {1, 2, 3}
    assert "ANSWER-2" not in "".join(b["text"] for b in qs[1]["answer_blocks"])
    assert "ANSWER-1" in "".join(b["text"] for b in qs[1]["answer_blocks"])
    assert "ANSWER-2" in "".join(b["text"] for b in qs[2]["answer_blocks"])
    assert qs[2]["structure_status"] == "processing_failed"
    assert qs[1]["association_status"] == qs[3]["association_status"] == "confirmed"
    pair = bank["pairs"][0]
    assert pair["original_discovered_numbers"] == [1, 3]
    assert pair["analysis_discovered_numbers"] == [1, 2, 3]
    assert pair["analysis_without_original"] == [2]
    assert bank["batch_status"] == "partial"


def test_common_missing_number_is_not_hidden_by_equal_observed_sets(tmp_path):
    bank = build_source_bank(
        sources(
            tmp_path,
            original=blocks((1, 3), declared=3),
            analysis=blocks((1, 3), analysis=True, declared=3),
        ),
        tmp_path / "bank",
    )
    assert set(questions(bank)) == {1, 3}
    assert bank["pairs"][0]["common_missing_numbers"] == [2]
    assert bank["batch_status"] == "partial"


def test_unknown_declared_total_stays_null_while_observed_gap_is_explicit(tmp_path):
    original, analysis = blocks((1, 3)), blocks((1, 3), analysis=True)
    original[0] = paragraph("一、选择题")
    analysis[0] = paragraph("一、选择题")
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    pair = bank["pairs"][0]
    assert pair["declared_question_count"] is None
    assert pair["common_missing_numbers"] == [2]
    assert "infer" in pair["common_missing_number_basis"]
    assert bank["batch_status"] == "partial"


def test_section_counts_are_local_not_a_global_last_declaration(tmp_path):
    original = [
        paragraph("一、选择题（共2题）"),
        paragraph("1. 合成题干1"),
        paragraph("2. 合成题干2"),
        paragraph("二、解答题（共1题）"),
        paragraph("3. 合成题干3"),
    ]
    analysis = [paragraph("一、选择题（共2题）")]
    for n in [1, 2, 3]:
        if n == 3:
            analysis.append(paragraph("二、解答题（共1题）"))
        analysis.extend(
            [
                paragraph(f"{n}. 合成题干{n}"),
                paragraph(f"【答案】ANSWER-{n}"),
                paragraph(f"【解析】EXPLANATION-{n}"),
            ]
        )
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    assert set(questions(bank)) == {1, 2, 3}
    assert bank["batch_status"] == "succeeded"
    assert all(q["association_status"] == "confirmed" for q in bank["questions"])


def test_answers_without_explanation_are_imported_and_available(tmp_path):
    bank = build_source_bank(
        sources(tmp_path, analysis=blocks(analysis=True, explanation=False)), tmp_path / "bank"
    )
    q = questions(bank)[1]
    assert q["structure_status"] == "imported"
    assert q["answer_available"] is True
    assert q["analysis_blocks"] == []
    assert bank["batch_status"] == "succeeded"


def test_empty_answer_marker_is_not_available(tmp_path):
    analysis = blocks(analysis=True)
    analysis[2] = paragraph("【答案】")
    bank = build_source_bank(sources(tmp_path, analysis=analysis), tmp_path / "bank")
    q = questions(bank)[1]
    assert q["answer_available"] is False
    assert q["structure_status"] == "processing_failed"
    assert any(i["code"] == "missing_answer" for i in q["issues"])


def test_inline_markers_keep_each_media_in_its_own_located_segment(tmp_path):
    inline = paragraph("【答案】")
    preview(inline, "answerPic")
    append_text(inline, "答案合成【解析】")
    preview(inline, "analysisPic")
    append_text(inline, "解析合成")
    red, blue = image_bytes("red"), image_bytes("blue")
    analysis = [paragraph("一、选择题（共1题）"), paragraph("1. 合成题干1"), inline]
    bank = build_source_bank(
        sources(
            tmp_path, analysis=analysis, analysis_media={"answerPic": red, "analysisPic": blue}
        ),
        tmp_path / "bank",
    )
    q = questions(bank)[1]
    assert q["structure_status"] == "imported"
    assert q["answer_available"] is True
    answer, reasoning = q["answer_blocks"][0], q["analysis_blocks"][0]
    assert "答案合成" in answer["text"] and "解析合成" not in answer["text"]
    assert "解析合成" in reasoning["text"] and "答案合成" not in reasoning["text"]
    assert answer["paragraph_index"] == reasoning["paragraph_index"]
    assert 0 <= answer["segment_index"] < reasoning["segment_index"]
    assert answer["source_text_range"][1] <= reasoning["source_text_range"][0]
    occurrences = answer["media_occurrences"] + reasoning["media_occurrences"]
    assert [o["source_rel_id"] for o in occurrences] == ["answerPic", "analysisPic"]
    assert (tmp_path / "bank" / answer["media_paths"][0]).read_bytes() == red
    assert (tmp_path / "bank" / reasoning["media_paths"][0]).read_bytes() == blue


def test_same_context_duplicate_files_keep_aliases_but_index_once(tmp_path):
    source = sources(tmp_path)
    original = source / "Synthetic（原卷版）.docx"
    duplicate = source / "Syn thetic（原卷版）.docx"
    shutil.copyfile(original, duplicate)
    bank = build_source_bank(source, tmp_path / "bank")
    assert len(bank["documents"]) == 2
    assert len(bank["questions"]) == 1
    assert bank["counts"]["input_files"] == 3
    assert bank["counts"]["duplicate_files"] == 1
    assert bank["batch_status"] == "succeeded"
    doc = next(d for d in bank["documents"] if d["role"] == "original")
    assert {doc["filename"], *doc["aliases"]} == {original.name, duplicate.name}
    assert (tmp_path / "bank/sources" / original.name).read_bytes() == original.read_bytes()
    assert (tmp_path / "bank/sources" / duplicate.name).read_bytes() == original.read_bytes()
    index_bank(bank, tmp_path / "bank")
    with sqlite3.connect(tmp_path / "bank/question-bank.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_same_source_bytes_in_different_contexts_have_distinct_record_ids(tmp_path):
    source = sources(tmp_path)
    shutil.copyfile(source / "Synthetic（原卷版）.docx", source / "Other（原卷版）.docx")
    shutil.copyfile(source / "精品解析：Synthetic（解析版）.docx", source / "Other（解析版）.docx")
    bank = build_source_bank(source, tmp_path / "bank")
    assert len(bank["documents"]) == len({d["id"] for d in bank["documents"]}) == 4
    assert len({d["sha256"] for d in bank["documents"]}) == 2
    assert len(bank["pairs"]) == len(bank["questions"]) == 2
    index_bank(bank, tmp_path / "bank")


def test_role_suffix_works_without_a_publisher_prefix(tmp_path):
    bank = build_source_bank(
        sources(tmp_path, analysis_name="Synthetic（解析版）.docx"), tmp_path / "bank"
    )
    assert {d["role"] for d in bank["documents"]} == {"original", "analysis"}
    assert questions(bank)[1]["association_status"] == "confirmed"
    assert bank["batch_status"] == "succeeded"


def test_no_question_section_is_failed_not_an_empty_success(tmp_path):
    bank = build_source_bank(
        sources(tmp_path, original=blocks()[1:], analysis=blocks(analysis=True)[1:]),
        tmp_path / "bank",
    )
    assert bank["questions"] == []
    assert bank["batch_status"] == "failed"
    assert bank["issues"]


def test_one_bad_file_keeps_successful_documents_and_failed_file_evidence(tmp_path):
    source = sources(tmp_path)
    bad = b"synthetic-invalid-docx"
    (source / "zz-bad.docx").write_bytes(bad)
    bank = build_source_bank(source, tmp_path / "bank")
    assert len(bank["documents"]) == 2
    assert len(bank["failed_documents"]) == 1
    assert bank["counts"]["input_files"] == 3
    assert bank["counts"]["failed_files"] == 1
    assert bank["batch_status"] == "partial"
    assert questions(bank)[1]["association_status"] == "confirmed"
    assert any(p.is_file() and p.read_bytes() == bad for p in (tmp_path / "bank").rglob("*"))


def fraction(node, *, unsupported=False):
    math = ET.SubElement(node, f"{{{M}}}oMath")
    f = ET.SubElement(math, f"{{{M}}}{'unknownFixture' if unsupported else 'f'}")
    for name, value in [("num", "x+1"), ("den", "2")]:
        part = ET.SubElement(f, f"{{{M}}}{name}")
        ET.SubElement(ET.SubElement(part, f"{{{M}}}r"), f"{{{M}}}t").text = value


def test_supported_omml_is_visible_and_counted_instead_of_silently_dropped(tmp_path):
    original, analysis = blocks(), blocks(analysis=True)
    fraction(original[1])
    fraction(analysis[1])
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    q = questions(bank)[1]
    fragment = html.fragment_fromstring(
        "".join(b["html"] for b in q["stem_blocks"]), create_parent="div"
    )
    assert fragment.xpath(".//*[local-name()='math']")
    assert fragment.xpath(".//*[local-name()='mfrac']")
    assert "x+1" in fragment.text_content() and "2" in fragment.text_content()
    assert sum(b["source_omml_count"] for b in q["stem_blocks"]) == 1
    assert sum(b["rendered_omml_count"] for b in q["stem_blocks"]) == 1
    assert bank["counts"]["source_omml_count"] == bank["counts"]["rendered_omml_count"] == 2
    assert q["structure_status"] == "imported"
    assert q["association_status"] == "confirmed"


def test_unknown_omml_is_explicitly_failed_without_erasing_source(tmp_path):
    original = blocks()
    fraction(original[1], unsupported=True)
    bank = build_source_bank(sources(tmp_path, original), tmp_path / "bank")
    q = questions(bank)[1]
    assert q["structure_status"] == "processing_failed"
    assert any("omml" in i["code"].lower() for i in q["issues"])
    assert q["practice_ready"] is False
    assert bank["batch_status"] != "succeeded"


def test_same_omml_text_with_different_fraction_structure_cannot_confirm(tmp_path):
    original, analysis = blocks(), blocks(analysis=True)
    fraction(original[1])
    fraction(analysis[1])
    texts = list(analysis[1].iter(f"{{{M}}}t"))
    texts[0].text, texts[1].text = "x", "+12"
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    q = questions(bank)[1]
    assert q["association_status"] == "unverified"
    assert any(i["code"] == "association_unverified" for i in q["issues"])
    assert q["structure_status"] == "imported"
    assert bank["batch_status"] == "partial"


def test_inline_math_only_answer_keeps_occurrence_in_its_own_segment(tmp_path):
    inline = paragraph("【答案】")
    fraction(inline)
    append_text(inline, "【解析】")
    fraction(inline)
    analysis = [paragraph("一、选择题（共1题）"), paragraph("1. 合成题干1"), inline]
    bank = build_source_bank(sources(tmp_path, analysis=analysis), tmp_path / "bank")
    q = questions(bank)[1]
    assert q["structure_status"] == "imported"
    assert q["answer_available"] is True
    assert bank["batch_status"] == "succeeded"
    for key in ("answer_blocks", "analysis_blocks"):
        assert sum(b["source_omml_count"] for b in q[key]) == 1
        assert sum(b["rendered_omml_count"] for b in q[key]) == 1
        assert sum(len(b["omml_occurrences"]) for b in q[key]) == 1
        fragment = html.fragment_fromstring("".join(b["html"] for b in q[key]), create_parent="div")
        assert len(fragment.xpath(".//*[local-name()='mfrac']")) == 1


def preview_pair(tmp_path, first, second):
    original, analysis = blocks(), blocks(analysis=True)
    preview(original[1], "formula")
    preview(analysis[1], "formula")
    return build_source_bank(
        sources(
            tmp_path,
            original,
            analysis,
            original_media={"formula": first},
            analysis_media={"formula": second},
        ),
        tmp_path / "bank",
    )


def test_same_text_but_different_formula_pixels_cannot_confirm(tmp_path):
    bank = preview_pair(tmp_path, image_bytes("red"), image_bytes("blue"))
    q = questions(bank)[1]
    assert q["association_status"] == "unverified"
    assert q["association_evidence"]["image_pixels_match"] is False
    assert q["structure_status"] == "imported"
    assert any(i["code"] == "association_unverified" for i in q["issues"])
    assert bank["batch_status"] == "partial"


def test_same_rgba_dimensions_with_different_png_metadata_can_confirm(tmp_path):
    first, second = image_bytes("red", "first"), image_bytes("red", "second")
    assert first != second
    bank = preview_pair(tmp_path, first, second)
    q = questions(bank)[1]
    assert q["association_status"] == "confirmed"
    assert q["association_evidence"]["image_pixels_match"] is True
    assert q["association_comparison_method"] == "source_association_v3"
    assert bank["batch_status"] == "succeeded"


def test_next_question_number_in_reasoning_does_not_create_a_duplicate(tmp_path):
    original, analysis = blocks((1, 2)), blocks((1, 2), analysis=True)
    analysis.insert(4, paragraph("2. 这是合成解析步骤，不是新的题干"))
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    assert set(questions(bank)) == {1, 2}
    assert all(q["structure_status"] == "imported" for q in bank["questions"])
    assert all(q["association_status"] == "confirmed" for q in bank["questions"])
    assert any("这是合成解析步骤" in b["text"] for b in questions(bank)[1]["analysis_blocks"])
    assert bank["batch_status"] == "succeeded"


def test_missing_middle_answer_cannot_pollute_previous_clean_explanation(tmp_path):
    original, analysis = blocks((1, 2, 3)), blocks((1, 2, 3), analysis=True)
    analysis = [p for p in analysis if "".join(p.itertext()) != "【答案】ANSWER-2"]
    bank = build_source_bank(sources(tmp_path, original, analysis), tmp_path / "bank")
    qs = questions(bank)
    assert set(qs) == {1, 2, 3}
    assert qs[2]["answer_available"] is False
    assert qs[2]["structure_status"] == "processing_failed"
    q1_text = "".join(b["text"] for b in qs[1]["analysis_blocks"])
    assert "2. 合成题干2" not in q1_text or qs[1]["structure_status"] == "processing_failed"
    assert qs[3]["structure_status"] == "imported"
    assert qs[3]["association_status"] == "confirmed"
    assert bank["batch_status"] == "partial"
