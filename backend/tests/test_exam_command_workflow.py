"""Synthetic CLI correction/summary regression; no live model or real student data."""

import json
import runpy
import sys
from pathlib import Path

import pytest
from app.exam_contracts import ExamModelResponse
from test_exam_analysis import _fixture, _run
from test_exam_cli import _evidence, _intake_payload

ROOT = Path(__file__).resolve().parents[2]


def test_response_cannot_borrow_another_questions_verified_paper_region(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts/analyze_student_exam.py"))
    intake = _intake_payload(tmp_path)
    first = _evidence("paper-1", "printed_question", (0.1, 0.1, 0.4, 0.4))
    second = _evidence("paper-1", "printed_question", (0.5, 0.5, 0.9, 0.9))
    response = ExamModelResponse(
        task="response",
        responses=[
            {
                "id": "synthetic-r1",
                "number": 1,
                "transcript": "synthetic",
                "legibility": "readable",
                "sources": [_evidence("response-1", "handwriting")],
                "paper_sources": [second],
                "paper_match": "matched",
            }
        ],
    )
    with pytest.raises(ValueError):
        namespace["validate_evidence"](
            response,
            intake,
            "response",
            set(),
            input_page_ids={"response-1", "paper-1"},
            upstream_evidence=[first, second],
            paper_evidence_by_number={1: [first], 2: [second]},
        )


def test_correct_and_summary_cli_read_report_envelopes_preserve_history(tmp_path, monkeypatch):
    from app.exam_view import render_exam_report

    assets = tmp_path / "assets"
    assets.mkdir()
    data = _fixture(assets, with_grade=True)
    base = _run(data)
    base_report = render_exam_report(
        base,
        intake=data["intake"],
        asset_root=assets,
        output_dir=tmp_path / "base",
        curriculum=data["curriculum"],
    )
    source = Path(base_report["json_path"])
    original = source.read_bytes()
    intake_file = tmp_path / "intake.json"
    intake_file.write_text(data["intake"].model_dump_json())
    curriculum_file = tmp_path / "curriculum.json"
    curriculum_file.write_text(data["curriculum"].model_dump_json())
    corrections = tmp_path / "corrections.json"
    corrections.write_text(
        json.dumps(
            [
                {
                    "id": "synthetic-c1",
                    "base_revision": base.revision,
                    "number": 1,
                    "response_id": "response-1",
                    "corrected_transcript": "synthetic corrected transcript",
                    "evidence": [
                        {
                            "page_id": "response-page",
                            "kind": "handwriting",
                            "bbox": [0.1, 0.1, 0.9, 0.45],
                        }
                    ],
                }
            ]
        )
    )
    namespace = runpy.run_path(str(ROOT / "scripts/analyze_student_exam.py"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze_student_exam.py",
            "correct",
            "--analysis",
            str(source),
            "--intake",
            str(intake_file),
            "--asset-root",
            str(assets),
            "--curriculum",
            str(curriculum_file),
            "--corrections",
            str(corrections),
            "--output",
            str(tmp_path / "corrected"),
        ],
    )
    namespace["main"]()
    assert source.read_bytes() == original
    changed_file = tmp_path / "corrected" / "report.json"
    changed = json.loads(changed_file.read_text())["analysis"]
    assert changed["parent_revision"] == base.revision
    assert changed["revision"] != base.revision
    assert changed["student_responses"][0]["transcript"] == "synthetic corrected transcript"
    assert changed["questions"][0]["reason_status"] == "unknown"
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze_student_exam.py",
            "summary",
            "--analyses",
            str(source),
            str(changed_file),
            "--student-id",
            base.student_id,
            "--curriculum",
            str(curriculum_file),
            "--output",
            str(summary),
        ],
    )
    namespace["main"]()
    assert json.loads(summary.read_text())["actual_exam_count"] == 1
