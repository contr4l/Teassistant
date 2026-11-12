"""Synthetic persistent worker integration; these tests never call a model."""

from dataclasses import replace

import pytest
from app import web_jobs
from app.prompt_templates import DEFAULT_PROMPTS, IMMUTABLE_GUARDRAILS, build_instructions
from app.web_settings import WebSettings
from app.web_store import Store
from test_bank_practice import _fixture


def setup(tmp_path):
    bank_dir, semantic_dir, curriculum, _ = _fixture(tmp_path)
    settings = WebSettings(
        database_url="sqlite://",
        data_dir=tmp_path / "private",
        bank_dir=bank_dir,
        semantic_dir=semantic_dir,
        curriculum_path=semantic_dir / "curriculum.json",
    )
    settings.data_dir.mkdir()
    store = Store(settings.database_url)
    store.init()
    user = store.create_user("synthetic-teacher", "Synthetic-password-123", "合成测试教师")
    store.ensure_prompts(DEFAULT_PROMPTS)
    student = store.create_record(
        "student",
        user["id"],
        {
            "name": "合成学生",
            "school": "合成学校",
            "grade": 9,
            "learned_point_ids": [p.id for p in curriculum.points],
            "progress_revision": 1,
            "is_synthetic_student": True,
        },
    )
    practice = store.create_record(
        "practice",
        user["id"],
        {
            "student_id": student["id"],
            "exam_ids": [],
            "quotas": [{"point_id": "p1", "count": 2}],
            "seed": 0,
            "layout": "compact",
        },
        parent_id=student["id"],
    )
    return store, settings, user, student, practice


def test_shortfall_publishes_preview_without_fake_downloads_or_curriculum_widening(tmp_path):
    store, settings, user, student, practice = setup(tmp_path)
    frozen = web_jobs.build_job_input(store, settings, "practice", practice)
    job = store.enqueue_job(user["id"], "practice", practice["id"], frozen)
    assert web_jobs.run_once(store, settings, "test-worker")
    done = store.get_job(job["id"], user["id"])
    result = store.get_record("practice", practice["id"], user["id"])
    assert done["status"] == "partial"
    assert done["result"]["selected"] == 1
    assert result["data"]["files"] == []
    assert result["data"]["snapshot"]["shortfalls"]
    assert result["data"]["snapshot"]["is_synthetic_student"] is True
    assert len(result["data"]["preview"]["items"]) == 1
    assert "data:image/" in result["data"]["preview"]["items"][0]["question_html"]


def test_progress_revoked_after_enqueue_fails_without_publishing(tmp_path):
    store, settings, user, student, practice = setup(tmp_path)
    frozen = web_jobs.build_job_input(store, settings, "practice", practice)
    job = store.enqueue_job(user["id"], "practice", practice["id"], frozen)
    changed = {**student["data"], "learned_point_ids": [], "progress_revision": 2}
    store.update_record(
        "student", student["id"], user["id"], changed, expected_revision=student["revision"]
    )
    web_jobs.run_once(store, settings, "test-worker")
    assert store.get_job(job["id"])["status"] == "failed"
    assert "snapshot" not in store.get_record("practice", practice["id"])["data"]


def test_prompt_edits_do_not_mutate_queued_snapshots_or_remove_fixed_guards(tmp_path):
    store, settings, user, student, practice = setup(tmp_path)
    old = web_jobs.build_job_input(store, settings, "practice", practice)
    active = next(row for row in store.list_prompts() if row["key"] == "report")
    store.save_prompt("report", "合成的新报告说明", user["id"], active["active_revision"])
    new = web_jobs.build_job_input(store, settings, "practice", practice)
    assert old["prompt_sha256"] != new["prompt_sha256"]
    assert old["prompt_snapshot"]["report"]["body"] == DEFAULT_PROMPTS["report"]["body"]
    assert (
        build_instructions("report", new["prompt_snapshot"])
        == IMMUTABLE_GUARDRAILS + "合成的新报告说明"
    )


def test_unexpected_worker_error_does_not_expose_source_text_or_secret(tmp_path, monkeypatch):
    store, settings, user, student, practice = setup(tmp_path)
    frozen = web_jobs.build_job_input(store, settings, "practice", practice)
    job = store.enqueue_job(user["id"], "practice", practice["id"], frozen)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-sensitive-key")

    def failed(*args):
        raise ValueError("synthetic student response; synthetic-sensitive-key")

    monkeypatch.setattr(web_jobs, "execute_job", failed)
    web_jobs.run_once(store, settings, "test-worker")
    done = store.get_job(job["id"])
    assert done["status"] == "failed"
    assert "student response" not in done["error"]
    assert "synthetic-sensitive-key" not in str(done)
    assert (
        "synthetic-sensitive-key"
        not in (settings.data_dir / "job-errors" / (job["id"] + ".log")).read_text()
    )


def test_unconfigured_bank_and_unknown_prompt_fail_explicitly(tmp_path):
    store, settings, user, student, practice = setup(tmp_path)
    with pytest.raises(ValueError, match="题库"):
        web_jobs.get_inventory(store, replace(settings, bank_dir=None), user["id"], student["id"])
    with pytest.raises(ValueError, match="prompt"):
        build_instructions("unregistered-stage")


def test_reanalysis_replays_repeated_teacher_edits_without_losing_latest(tmp_path):
    from app.exam_contracts import ExamCorrection
    from test_exam_analysis import _fixture as exam_fixture
    from test_exam_analysis import _run

    data = exam_fixture(tmp_path, with_grade=True)
    original = _run(data)
    response = original.student_responses[0]
    edits = [
        ExamCorrection(
            id=f"synthetic-edit-{index}",
            base_revision=original.revision,
            number=response.number,
            response_id=response.id,
            corrected_transcript=text,
            evidence=response.sources,
        ).model_dump(mode="json")
        for index, text in enumerate(("第一次老师修正", "第二次老师修正"))
    ]
    result = web_jobs.restore_manual_corrections(
        original, edits, intake=data["intake"], curriculum=data["curriculum"]
    )
    assert result.student_responses[0].transcript == "第二次老师修正"
    assert len(result.correction_ids) == 2
    assert original.student_responses[0].transcript != result.student_responses[0].transcript
