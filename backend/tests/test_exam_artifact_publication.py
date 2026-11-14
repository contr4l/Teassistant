"""Synthetic worker publication regression for exam intake artifacts."""

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from app import web_jobs
from app.exam_analysis import analyze_exam
from app.exam_contracts import ExamCorrection, ExamIntake
from test_web_acceptance import _http_exam, _png

pytest_plugins = ["test_web_acceptance"]


@pytest.mark.parametrize("include_intake_metadata", [True, False])
def test_worker_requires_and_publishes_cli_intake_metadata(
    web_http, monkeypatch, include_intake_metadata
):
    client = web_http["clients"]["teacher-a"]
    exam = _http_exam(web_http)
    upload_url = f"/api/exams/{exam['id']}/assets"
    for role, filename in (("paper", "paper.png"), ("response", "response.png")):
        response = client.post(
            upload_url,
            data={"role": role},
            files={"file": (filename, _png(), "image/png")},
        )
        assert response.status_code == 201, response.text

    store = web_http["store"]
    owner_id = web_http["users"]["teacher-a"]["id"]
    resource = store.get_record("exam", exam["id"], owner_id)
    frozen = web_jobs.build_job_input(store, web_http["settings"], "analysis", resource)
    frozen["provider"] = "codex"
    job = store.enqueue_job(owner_id, "analysis", exam["id"], frozen)

    def fake_cli(args, _cwd, timeout=3600, *, env=None):
        assert timeout == 3600
        assert env is None
        intake_path = args[args.index("--intake") + 1]
        curriculum_path = args[args.index("--curriculum") + 1]
        output = Path(args[args.index("--output") + 1])
        output.mkdir(parents=True)
        original = Path(intake_path).read_bytes()
        intake = ExamIntake.model_validate_json(original)
        # The fake CLI writes its transformed manifest only under run/. The
        # images remain under the original intake asset root.
        (output / "intake-with-regions.json").write_bytes(original)
        curriculum_bytes = Path(curriculum_path).read_bytes()
        from app.contracts import Curriculum

        analysis = analyze_exam(
            intake=intake,
            paper_results=[],
            response_results=[],
            diagnosis_results=[],
            reference_results=[],
            curriculum=Curriculum.model_validate_json(curriculum_bytes),
            exam_date=None,
        )
        analysis_path = output / f"analysis-{analysis.revision}.json"
        analysis_path.write_text(analysis.model_dump_json())
        metadata = {
            "schema_version": "exam-run-v1",
            "status": "partial",
            "provider": "codex",
            "intake_sha256": hashlib.sha256(original).hexdigest(),
            "curriculum_sha256": hashlib.sha256(curriculum_bytes).hexdigest(),
            "analysis_path": str(analysis_path),
            "analysis_revision": analysis.revision,
            "unresolved_question_count": len(analysis.questions),
            "teacher_report_path": None,
        }
        if include_intake_metadata:
            metadata["intake_path"] = str(output / "intake-with-regions.json")
            metadata["asset_root"] = str(Path(intake_path).resolve().parent)
        (output / "run.json").write_text(json.dumps(metadata))

    monkeypatch.setattr(web_jobs, "_subprocess_run", fake_cli)
    worker_settings = replace(
        web_http["settings"], allow_codex=True, codex_model="synthetic-fake-provider"
    )
    assert web_jobs.run_once(store, worker_settings, "synthetic-worker")
    job_result = store.get_job(job["id"])
    if not include_intake_metadata:
        assert job_result["status"] == "failed"
        unpublished = store.get_record("exam", exam["id"], owner_id)["data"]
        assert "intake_path" not in unpublished
        assert not unpublished.get("analysis_path")
        return
    assert job_result["status"] == "partial", job_result.get("error")

    published = store.get_record("exam", exam["id"], owner_id)["data"]
    assert published["intake_path"].startswith(f"jobs/{job['id']}/")
    assert published["intake_path"].endswith("/run/intake-with-regions.json")
    assert (
        published["asset_root"]
        == published["intake_path"].removesuffix("/run/intake-with-regions.json") + "/intake"
    )

    api = f"/api/exams/{exam['id']}/analysis"
    response = client.get(api)
    assert response.status_code == 200, response.text
    assert len(response.json()["pages"]) == 2
    assert client.get(response.json()["pages"][0]["url"]).status_code == 200


def test_correction_migrates_legacy_manifest_pointer_to_run_manifest(web_http, tmp_path):
    from test_exam_analysis import _fixture as exam_fixture
    from test_exam_analysis import _run

    exam = _http_exam(web_http)
    owner_id = web_http["users"]["teacher-a"]["id"]
    store = web_http["store"]
    original = exam_fixture(tmp_path, with_grade=True)
    intake = original["intake"].model_copy(
        update={"exam_id": exam["id"], "student_id": exam["student_id"]}
    )
    base = _run({**original, "intake": intake}).model_copy(
        update={"exam_id": exam["id"], "student_id": exam["student_id"]}
    )
    response = base.student_responses[0]
    correction = ExamCorrection(
        id="synthetic-legacy-correction",
        base_revision=base.revision,
        number=response.number,
        response_id=response.id,
        corrected_transcript="修正后的合成作答",
        evidence=response.sources,
    ).model_dump(mode="json")
    source = store.get_record("exam", exam["id"], owner_id)
    legacy_job = "a" * 32
    lease = "synthetic-lease"
    asset_relative = f"jobs/{legacy_job}/{lease}/intake"
    old_pointer = f"{asset_relative}/intake-with-regions.json"
    asset_root = web_http["settings"].data_dir / asset_relative
    (asset_root / "pages").mkdir(parents=True)
    (asset_root / "intake.json").write_text(intake.model_dump_json())
    for page in intake.pages:
        shutil.copyfile(original["asset_root"] / page.image_path, asset_root / page.image_path)
    run_dir = asset_root.parent / "run"
    run_dir.mkdir()
    (run_dir / "intake-with-regions.json").write_text(intake.model_dump_json())

    curriculum_path = tmp_path / "curriculum.json"
    curriculum_path.write_text(original["curriculum"].model_dump_json())
    settings = replace(web_http["settings"], curriculum_path=curriculum_path)
    analysis_path = settings.data_dir / "base-analysis.json"
    analysis_path.write_text(base.model_dump_json())
    data = {
        **source["data"],
        "student_id": exam["student_id"],
        "question_count": len(intake.expected_question_numbers),
        "analysis_path": "base-analysis.json",
        "analysis_revision": base.revision,
        "intake_path": old_pointer,
        "asset_root": asset_relative,
    }
    store.update_record("exam", exam["id"], owner_id, data, source["revision"])
    frozen = {
        "resource_data": data,
        "student_id": exam["student_id"],
        "analysis_revision": base.revision,
        "analysis_sha256": hashlib.sha256(analysis_path.read_bytes()).hexdigest(),
        "corrections": [correction],
        "provider": "codex",
        "prompt_snapshot": store.snapshot_prompts(),
    }
    result, updated, status = web_jobs._correction(
        store,
        settings,
        {
            "input_data": frozen,
            "resource_id": exam["id"],
            "owner_id": owner_id,
        },
        settings.data_dir / "correction-job",
    )
    assert status == "partial"
    assert updated["intake_path"] == f"jobs/{legacy_job}/{lease}/run/intake-with-regions.json"
    assert updated["manual_corrections"] == [correction]
    assert result["analysis_revision"] != base.revision
