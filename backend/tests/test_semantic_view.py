from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest
from app.contracts import Curriculum
from app.semantic_view import render_semantic_report
from PIL import Image


def _fixture(tmp_path: Path):
    input_dir = tmp_path / "inputs"
    image_dir = input_dir / "images"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "q.png"
    Image.new("RGB", (120, 80), "white").save(image_path, format="PNG")
    image = {
        "path": "images/q.png",
        "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "width": 120,
        "height": 80,
    }
    curriculum = Curriculum(
        version="junior-math-demand-v1",
        is_synthetic=False,
        points=[
            {"id": "fractions", "name": "分数运算", "prerequisite_ids": ["integers"]},
            {"id": "integers", "name": "整数运算", "prerequisite_ids": []},
            {"id": "real", "name": "实数", "prerequisite_ids": []},
        ],
    )
    rows = {
        "q1": {
            "status": "assessed",
            "association_status": "confirmed",
            "assessment": {
                "point_ids": ["fractions"],
                "prerequisite_ids": ["integers"],
                "difficulty_score": 55,
                "reasoning": "分步化简 <script>alert('bad')</script>",
                "point_evidence": [
                    {"point_id": "fractions", "role": "target", "reason": "题目要求通分"},
                    {"point_id": "integers", "role": "prerequisite", "reason": "用到整数计算"},
                ],
                "warnings": ["<img src=x onerror=alert(1)>", "https://model.invalid"],
            },
            "required_point_ids": ["fractions", "integers", "real"],
            "error_code": None,
        },
        "q2": {
            "status": "failed",
            "association_status": "unverified",
            "assessment": None,
            "required_point_ids": [],
            "error_code": "invalid_assessment",
        },
        "q3": {
            "status": "not_attempted",
            "association_status": "confirmed",
            "assessment": None,
            "required_point_ids": [],
            "error_code": None,
        },
    }
    inputs = {
        qid: {
            "association_status": row["association_status"],
            "reliable_solution_used": row["association_status"] == "confirmed",
            "text": "题目文本",
            "images": [image],
        }
        for qid, row in rows.items()
    }
    return input_dir, rows, curriculum, inputs


def test_static_report_copies_images_and_separates_program_closure(tmp_path):
    input_dir, rows, curriculum, inputs = _fixture(tmp_path)
    output = tmp_path / "report"

    index = render_semantic_report(output, rows, curriculum, inputs, input_dir)

    page = index.read_text(encoding="utf-8")
    assert "AI考点与难度初评" in page
    assert "实际分母<b>3</b>" in page
    assert "未确认题答关联<b>1</b>" in page
    assert "关联未确认：答案/解析未用，本题未完成评级" in page
    assert "程序补足的前置闭包（不是模型显式证据）" in page
    assert "real" in page
    assert "&lt;script&gt;alert(&#x27;bad&#x27;)&lt;/script&gt;" in page
    assert "<img src=x onerror=alert(1)>" not in page
    assert 'href="https://model.invalid"' not in page
    assert "annotations.csv" in page
    copied = output / "preview-images/q001-q1/tile-001.png"
    assert copied.is_file()
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == inputs["q1"]["images"][0]["sha256"]

    with (output / "annotations.csv").open(encoding="utf-8-sig", newline="") as stream:
        table = list(csv.reader(stream))
    assert len(table) == 4
    assert table[0][0:3] == ["题目ID", "状态", "题答关联"]
    assert table[1][0] == "q1"
    assert table[1][9:11] == ["55", "提高"]
    assert table[1][7:9] == ["real", "实数 [real]"]
    assert table[2][1] == "失败"
    assert table[3][1] == "未尝试"


def test_report_is_idempotent_for_same_images_and_rejects_changed_existing_image(tmp_path):
    input_dir, rows, curriculum, inputs = _fixture(tmp_path)
    output = tmp_path / "report"
    index = render_semantic_report(output, rows, curriculum, inputs, input_dir)
    image = output / "preview-images/q001-q1/tile-001.png"
    original_image = image.read_bytes()

    assert render_semantic_report(output, rows, curriculum, inputs, input_dir) == index
    assert image.read_bytes() == original_image
    assert index.is_file()
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs from source"):
        render_semantic_report(output, rows, curriculum, inputs, input_dir)


def test_rejects_input_path_escape_and_manifest_hash_mismatch(tmp_path):
    input_dir, rows, curriculum, inputs = _fixture(tmp_path)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2), "red").save(outside, format="PNG")
    inputs["q1"]["images"][0]["path"] = "../outside.png"
    with pytest.raises(ValueError, match="escapes"):
        render_semantic_report(tmp_path / "escape-report", rows, curriculum, inputs, input_dir)

    inputs["q1"]["images"][0]["path"] = "images/q.png"
    inputs["q1"]["images"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        render_semantic_report(tmp_path / "hash-report", rows, curriculum, inputs, input_dir)
    assert not (tmp_path / "hash-report").exists()


def test_csv_formula_injection_is_neutralized(tmp_path):
    input_dir, rows, curriculum, inputs = _fixture(tmp_path)
    rows["=HYPERLINK(1)"], inputs["=HYPERLINK(1)"] = rows.pop("q3"), inputs.pop("q3")

    report = render_semantic_report(tmp_path / "report", rows, curriculum, inputs, input_dir)

    with (report.parent / "annotations.csv").open(encoding="utf-8-sig", newline="") as stream:
        table = list(csv.reader(stream))
    assert any(row[0] == "'=HYPERLINK(1)" for row in table[1:])


def test_row_and_input_association_provenance_must_match(tmp_path):
    input_dir, rows, curriculum, inputs = _fixture(tmp_path)
    inputs["q2"]["reliable_solution_used"] = True
    with pytest.raises(ValueError, match="solution provenance"):
        render_semantic_report(tmp_path / "bad-report", rows, curriculum, inputs, input_dir)
