"""Synthetic guards for per-call evidence and resume orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.exam_contracts import ExamEvidence, ExamIntake, ExamModelResponse
from app.exam_models import ExamModelResult
from PIL import Image

_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "analyze_student_exam.py"
_CLI_SPEC = importlib.util.spec_from_file_location("analyze_student_exam", _CLI_PATH)
assert _CLI_SPEC is not None and _CLI_SPEC.loader is not None
analyze_student_exam = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(analyze_student_exam)


def _intake_payload(
    root: Path, *, response_pages: int = 1, blank_response: bool = False
) -> ExamIntake:
    materials = []
    pages = []
    for role in ("paper", "response", "reference"):
        source = root / "sources" / f"{role}.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"synthetic-{role}".encode())
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        page_ids = []
        count = response_pages if role == "response" else 1
        for number in range(1, count + 1):
            page_id = f"{role}-{number}"
            image_path = root / "pages" / f"{page_id}.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (20, 12), "white")
            if role == "response" and not blank_response:
                image.putpixel((0, 0), (0, 0, 0))
            image.save(image_path)
            image_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
            pages.append(
                {
                    "id": page_id,
                    "document_sha256": source_sha,
                    "source_page": number,
                    "role": role,
                    "image_path": f"pages/{page_id}.png",
                    "image_sha256": image_sha,
                    "width": 20,
                    "height": 12,
                }
            )
            page_ids.append(page_id)
        materials.append(
            {
                "id": f"material-{role}",
                "sha256": source_sha,
                "source_path": f"sources/{role}.pdf",
                "role": role,
                "page_ids": page_ids,
            }
        )
    return ExamIntake.model_validate(
        {
            "exam_id": "synthetic-exam",
            "student_id": "synthetic-student",
            "is_synthetic_student": True,
            "expected_question_numbers": [1, 2],
            "materials": materials,
            "pages": pages,
        }
    )


def _evidence(page_id: str, kind: str, bbox=(0.1, 0.1, 0.8, 0.8)) -> dict:
    return {"page_id": page_id, "bbox": bbox, "kind": kind, "excerpt": "synthetic"}


def test_call_rejects_existing_intake_evidence_from_a_page_not_attached(tmp_path: Path) -> None:
    intake = _intake_payload(tmp_path)
    response = ExamModelResponse(
        task="paper",
        questions=[
            {
                "number": 1,
                "text": "synthetic question",
                "sources": [_evidence("reference-1", "reference")],
            }
        ],
    )

    with pytest.raises(ValueError, match="not provided to this call"):
        analyze_student_exam.validate_evidence(
            response,
            intake,
            "paper",
            set(),
            input_page_ids={"paper-1"},
        )


def test_diagnosis_cannot_emit_question_outside_current_batch(tmp_path: Path) -> None:
    intake = _intake_payload(tmp_path)
    response = ExamModelResponse(
        task="diagnosis",
        diagnoses=[{"number": 2, "outcome": "unknown", "reason_status": "unknown"}],
    )

    with pytest.raises(ValueError, match="unexpected question number"):
        analyze_student_exam.validate_evidence(
            response,
            intake,
            "diagnosis",
            set(),
            input_page_ids={"paper-1", "response-1"},
            question_numbers={1},
        )


def test_response_paper_sources_accept_only_exact_upstream_claims(tmp_path: Path) -> None:
    intake = _intake_payload(tmp_path)
    source = _evidence("paper-1", "printed_question")
    response = ExamModelResponse(
        task="response",
        responses=[
            {
                "id": "synthetic-r1",
                "number": 1,
                "transcript": "synthetic work",
                "legibility": "readable",
                "sources": [_evidence("response-1", "handwriting")],
                "paper_match": "matched",
                "association_reason": "synthetic evidence-based link",
                "paper_sources": [source],
            }
        ],
    )
    analyze_student_exam.validate_evidence(
        response,
        intake,
        "response",
        set(),
        input_page_ids={"response-1"},
        upstream_evidence=[source],
    )

    response.responses[0].paper_sources = [
        ExamEvidence.model_validate(_evidence("paper-1", "printed_question", (0.2, 0.2, 0.9, 0.9)))
    ]
    with pytest.raises(ValueError, match="not provided to this call"):
        analyze_student_exam.validate_evidence(
            response,
            intake,
            "response",
            set(),
            input_page_ids={"response-1"},
            upstream_evidence=[source],
        )


@pytest.mark.parametrize(
    "paper_source,upstream",
    [
        (_evidence("response-1", "handwriting"), []),
        (_evidence("reference-1", "reference"), [_evidence("reference-1", "reference")]),
    ],
)
def test_response_paper_sources_cannot_use_nonpaper_evidence(
    tmp_path: Path, paper_source: dict, upstream: list[dict]
) -> None:
    intake = _intake_payload(tmp_path)
    response = ExamModelResponse(
        task="response",
        responses=[
            {
                "id": "synthetic-r1",
                "number": 1,
                "transcript": "synthetic work",
                "legibility": "readable",
                "sources": [_evidence("response-1", "handwriting")],
                "paper_match": "matched",
                "association_reason": "synthetic evidence-based link",
                "paper_sources": [paper_source],
            }
        ],
    )

    with pytest.raises(ValueError):
        analyze_student_exam.validate_evidence(
            response,
            intake,
            "response",
            set(),
            input_page_ids={"response-1"},
            upstream_evidence=upstream,
        )


def test_role_mismatch_and_stage_field_mismatch_are_rejected(tmp_path: Path) -> None:
    intake = _intake_payload(tmp_path)
    wrong_role = ExamModelResponse(
        task="paper",
        questions=[
            {
                "number": 1,
                "text": "synthetic question",
                "sources": [_evidence("response-1", "printed_question")],
            }
        ],
    )
    with pytest.raises(ValueError, match="wrong material role"):
        analyze_student_exam.validate_evidence(
            wrong_role,
            intake,
            "paper",
            set(),
            input_page_ids={"response-1"},
        )

    mixed_stage = ExamModelResponse(
        task="paper",
        questions=[
            {
                "number": 1,
                "text": "synthetic question",
                "sources": [_evidence("paper-1", "printed_question")],
            }
        ],
        diagnoses=[{"number": 1, "outcome": "unknown", "reason_status": "unknown"}],
    )
    with pytest.raises(ValueError, match="mixed results from different stages"):
        analyze_student_exam.validate_evidence(
            mixed_stage,
            intake,
            "paper",
            set(),
            input_page_ids={"paper-1"},
        )


def _write_run_inputs(root: Path) -> tuple[Path, Path, ExamIntake]:
    intake = _intake_payload(root, response_pages=1)
    intake_path = root / "intake.json"
    intake_path.write_text(intake.model_dump_json(), encoding="utf-8")
    curriculum_path = root / "curriculum.json"
    curriculum_path.write_text(
        json.dumps(
            {
                "version": "synthetic-v1",
                "is_synthetic": True,
                "points": [{"id": "synthetic-p1", "name": "Synthetic point"}],
            }
        ),
        encoding="utf-8",
    )
    return intake_path, curriculum_path, intake


def _args(intake: Path, curriculum: Path, output: Path, *, resume=False) -> SimpleNamespace:
    return SimpleNamespace(
        intake=intake,
        curriculum=curriculum,
        output=output,
        provider="codex",
        codex_model="synthetic-model",
        codex_executable="python",
        timeout=5,
        concurrency=1,
        exam_date=None,
        stage="layout",
        resume=resume,
    )


def test_fake_adapter_rejected_evidence_is_preserved_in_failed_call_record(
    tmp_path: Path, monkeypatch
) -> None:
    intake_path, curriculum_path, _intake = _write_run_inputs(tmp_path / "bundle")
    output = tmp_path / "analysis"
    raw = {
        "task": "layout",
        "regions": [
            {
                "page_id": "paper-1",
                "bbox": [0.1, 0.1, 0.9, 0.9],
                "role": "paper",
                "question_numbers": [1],
            }
        ],
    }

    class FakeAdapter:
        def __init__(self, _config):
            pass

        async def assess(self, **_kwargs):
            return ExamModelResult(
                provider="synthetic",
                model="synthetic-model",
                attempts=1,
                usage=None,
                raw_json_result=raw,
                elapsed_seconds=0.01,
            )

    monkeypatch.setattr("app.exam_models.CodexExamAdapter", FakeAdapter)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/true")
    asyncio.run(analyze_student_exam.run(_args(intake_path, curriculum_path, output)))

    run_record = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "layout_partial"
    assert run_record["failures"][0]["error_code"] == "ValueError"
    call_record = next((output / "calls").glob("*.json"))
    saved = json.loads(call_record.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["raw_json_result"] == raw


def test_resume_rejects_input_drift_before_any_model_call(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    # The response page is blank, so a layout-only run never calls a model.
    intake = _intake_payload(root, blank_response=True)
    intake_path = root / "intake.json"
    intake_path.write_text(intake.model_dump_json(), encoding="utf-8")
    curriculum_path = root / "curriculum.json"
    curriculum_path.write_text(
        json.dumps(
            {
                "version": "synthetic-v1",
                "is_synthetic": True,
                "points": [{"id": "synthetic-p1", "name": "Synthetic point"}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "analysis"
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/true")

    asyncio.run(analyze_student_exam.run(_args(intake_path, curriculum_path, output)))
    curriculum_path.write_text(
        json.dumps(
            {
                "version": "synthetic-v2",
                "is_synthetic": True,
                "points": [{"id": "synthetic-p1", "name": "Synthetic point"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity changed"):
        asyncio.run(
            analyze_student_exam.run(_args(intake_path, curriculum_path, output, resume=True))
        )
