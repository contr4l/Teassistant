"""Synthetic repair diagnostics never call a model or rewrite student artifacts."""
from __future__ import annotations

import copy
import importlib.util
import json
import stat
import uuid
from pathlib import Path

import pytest
from app.contracts import Curriculum
from app.exam_analysis import analyze_exam
from app.exam_intake import prepare_exam_intake
from PIL import Image
from test_web_jobs import setup

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("repair_exam_intake_paths", ROOT / "scripts/repair_exam_intake_paths.py")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def _repair_fixture(tmp_path):
    store, settings, teacher, student, _practice = setup(tmp_path)
    exam = store.create_record("exam", teacher["id"], {"student_id": student["id"], "question_count": 1})
    job_id = uuid.uuid4().hex
    assert job_id != exam["id"]
    job_root = settings.data_dir / "jobs" / job_id / uuid.uuid4().hex
    intake_root, run_root = job_root / "intake", job_root / "run"
    sources = tmp_path / "synthetic-originals"
    sources.mkdir()
    for name in ("paper", "response"):
        Image.new("RGB", (12, 18), "white").save(sources / (name + ".png"))
    prepare_exam_intake(exam_id=exam["id"], student_id=student["id"], expected_question_numbers=[1],
                        paper_paths=[sources / "paper.png"], response_paths=[sources / "response.png"],
                        reference_paths=[], output_dir=intake_root)
    from app.exam_contracts import ExamIntake
    intake = ExamIntake.model_validate_json((intake_root / "intake.json").read_bytes())
    run_root.mkdir()
    (run_root / "intake-with-regions.json").write_text(intake.model_dump_json())
    curriculum = Curriculum.model_validate_json(settings.curriculum_path.read_bytes())
    analysis = analyze_exam(intake=intake, paper_results=[], response_results=[], diagnosis_results=[],
                            curriculum=curriculum, exam_date=None)
    analysis_file = run_root / ("analysis-" + analysis.revision + ".json")
    analysis_file.write_text(analysis.model_dump_json())
    def relative(path):
        return path.relative_to(settings.data_dir).as_posix()
    data = {**exam["data"], "analysis_path": relative(analysis_file),
            "analysis_revision": analysis.revision,
            "intake_path": relative(intake_root / "intake-with-regions.json"),
            "asset_root": relative(intake_root), "status": "partial",
            "teacher_report": {"synthetic": "preserve report"},
            "manual_corrections": [{"synthetic": "preserve corrections"}]}
    exam = store.update_record("exam", exam["id"], teacher["id"], data, exam["revision"])
    return store, settings, teacher, exam, intake, run_root, relative(run_root / "intake-with-regions.json")


def test_diagnose_is_read_only_and_apply_changes_only_verified_pointer(tmp_path):
    store, settings, teacher, exam, _intake, _run, target = _repair_fixture(tmp_path)
    before = copy.deepcopy(exam)
    files = {p.relative_to(settings.data_dir): p.read_bytes()
             for p in settings.data_dir.rglob("*") if p.is_file()}
    report = repair.diagnose_or_repair(store, settings)
    assert report["counts"] == {"legacy_pointer_repairable": 1}
    assert store.get_record("exam", exam["id"], teacher["id"]) == before
    assert not (settings.data_dir / "maintenance").exists()
    applied = repair.diagnose_or_repair(store, settings, apply=True)
    assert applied["counts"]["repaired"] == 1
    after = store.get_record("exam", exam["id"], teacher["id"])
    assert after["revision"] == before["revision"] + 1
    assert after["data"] == {**before["data"], "intake_path": target}
    for relative, raw in files.items():
        assert (settings.data_dir / relative).read_bytes() == raw
    audit = next((settings.data_dir / "maintenance").glob("*.json"))
    assert stat.S_IMODE(audit.stat().st_mode) == 0o600
    assert json.loads(audit.read_text())["state"] == "applied"
    again = repair.diagnose_or_repair(store, settings, apply=True)
    assert again["counts"] == {"current_pointer_valid": 1}
    assert len(list(audit.parent.glob("*.json"))) == 1


@pytest.mark.parametrize("change", ["missing_analysis", "wrong_student", "bad_revision", "bad_material", "bad_page", "bad_source_file"])
def test_repair_refuses_unverified_artifacts(tmp_path, change):
    store, settings, teacher, exam, intake, run, _target = _repair_fixture(tmp_path)
    analysis_file = settings.data_dir / exam["data"]["analysis_path"]
    if change == "missing_analysis":
        analysis_file.unlink()
    elif change in {"wrong_student", "bad_revision", "bad_material"}:
        data = json.loads(analysis_file.read_text())
        key, value = {"wrong_student": ("student_id", "foreign"),
                      "bad_revision": ("revision", "foreign"),
                      "bad_material": ("source_material_sha256", "0" * 64)}[change]
        data[key] = value
        analysis_file.write_text(json.dumps(data))
    elif change == "bad_source_file":
        (settings.data_dir / exam["data"]["asset_root"] / intake.materials[0].source_path).write_bytes(b"corrupt")
    else:
        (settings.data_dir / exam["data"]["asset_root"] / intake.pages[0].image_path).write_bytes(b"corrupt")
    result = repair.diagnose_or_repair(store, settings, apply=True)
    assert not result["counts"].get("repaired")
    assert store.get_record("exam", exam["id"], teacher["id"])["data"] == exam["data"]


def test_queued_job_prevents_repair(tmp_path):
    store, settings, teacher, exam, _intake, _run, _target = _repair_fixture(tmp_path)
    store.enqueue_job(teacher["id"], "correction", exam["id"], {})
    result = repair.diagnose_or_repair(store, settings, apply=True)
    assert result["counts"] == {"active_job_skipped": 1}
    assert store.get_record("exam", exam["id"], teacher["id"])["data"] == exam["data"]


@pytest.mark.parametrize("race", ["revision", "active_job"])
def test_compare_and_swap_rejects_a_change_after_diagnosis(tmp_path, monkeypatch, race):
    store, settings, teacher, exam, _intake, _run, _target = _repair_fixture(tmp_path)
    original = repair._verified_candidate
    calls = []

    def candidate(*args):
        result = original(*args)
        calls.append(1)
        if len(calls) == 2:
            if race == "revision":
                store.update_record("exam", exam["id"], teacher["id"],
                                    {**exam["data"], "concurrent": True}, exam["revision"])
            else:
                store.enqueue_job(teacher["id"], "analysis", exam["id"], {})
        return result

    monkeypatch.setattr(repair, "_verified_candidate", candidate)
    result = repair.diagnose_or_repair(store, settings, apply=True)
    assert result["counts"]["conflict"] == 1
    assert not result["counts"].get("repaired")
    assert store.get_record("exam", exam["id"], teacher["id"])["data"]["intake_path"] == exam["data"]["intake_path"]
