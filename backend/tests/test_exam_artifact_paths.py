from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.exam_artifacts import ArtifactUnavailable, read_exam_intake
from app.exam_contracts import ExamIntake
from app.web_api import create_app
from app.web_settings import WebSettings
from app.web_store import Store
from fastapi.testclient import TestClient
from PIL import Image


def _intake(exam_id: str, student_id: str, image_path: str = "pages/page.png") -> dict:
    digest = hashlib.sha256(b"synthetic-image").hexdigest()
    return {
        "schema_version": "exam-intake-v1",
        "exam_id": exam_id,
        "student_id": student_id,
        "is_synthetic_student": True,
        "expected_question_numbers": [1],
        "materials": [
            {
                "id": "material-1",
                "sha256": digest,
                "source_path": "paper.png",
                "role": "paper",
                "page_ids": ["page-1"],
            }
        ],
        "pages": [
            {
                "id": "page-1",
                "document_sha256": digest,
                "source_page": 1,
                "role": "paper",
                "image_path": image_path,
                "image_sha256": digest,
                "width": 3,
                "height": 2,
            }
        ],
    }


def _legacy_paths(root: Path, job_id: str, lease: str = "lease-1") -> tuple[str, str, Path, Path]:
    intake_relative = f"jobs/{job_id}/{lease}/intake/intake-with-regions.json"
    asset_relative = f"jobs/{job_id}/{lease}/intake"
    intake_dir = root / asset_relative
    run_dir = root / f"jobs/{job_id}/{lease}/run"
    intake_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    return intake_relative, asset_relative, intake_dir, run_dir


def test_reads_exact_legacy_run_manifest_for_matching_identity(tmp_path):
    exam_id, student_id, job_id = "a" * 32, "student-a", "1" * 32
    settings = WebSettings(data_dir=tmp_path)
    intake_relative, asset_relative, intake_dir, run_dir = _legacy_paths(tmp_path, job_id)
    expected = _intake(exam_id, student_id)
    (intake_dir / "intake.json").write_text(json.dumps(expected))
    (run_dir / "intake-with-regions.json").write_text(json.dumps(expected))

    intake, effective_path = read_exam_intake(
        settings,
        {"intake_path": intake_relative, "asset_root": asset_relative, "question_count": 1},
        exam_id=exam_id,
        student_id=student_id,
    )
    assert isinstance(intake, ExamIntake)
    assert intake.exam_id == exam_id
    assert effective_path == f"jobs/{job_id}/lease-1/run/intake-with-regions.json"


def test_corrupt_existing_primary_never_falls_back(tmp_path):
    exam_id, student_id, job_id = "b" * 32, "student-b", "2" * 32
    settings = WebSettings(data_dir=tmp_path)
    intake_relative, asset_relative, intake_dir, run_dir = _legacy_paths(tmp_path, job_id)
    (intake_dir / "intake.json").write_text(json.dumps(_intake(exam_id, student_id)))
    (intake_dir / "intake-with-regions.json").write_text("{")
    (run_dir / "intake-with-regions.json").write_text(json.dumps(_intake(exam_id, student_id)))

    with pytest.raises(ArtifactUnavailable):
        read_exam_intake(
            settings,
            {"intake_path": intake_relative, "asset_root": asset_relative},
            exam_id=exam_id,
            student_id=student_id,
        )


def test_legacy_fallback_rejects_shared_job_cross_reference(tmp_path):
    exam_id, another_exam, student_id, job_id = "c" * 32, "d" * 32, "student-c", "3" * 32
    settings = WebSettings(data_dir=tmp_path)
    intake_relative, asset_relative, intake_dir, run_dir = _legacy_paths(tmp_path, job_id)
    (intake_dir / "intake.json").write_text(json.dumps(_intake(exam_id, student_id)))
    (run_dir / "intake-with-regions.json").write_text(json.dumps(_intake(another_exam, student_id)))

    with pytest.raises(ArtifactUnavailable):
        read_exam_intake(
            settings,
            {"intake_path": intake_relative, "asset_root": asset_relative},
            exam_id=exam_id,
            student_id=student_id,
        )


def test_asset_root_must_match_original_manifest_identity(tmp_path):
    exam_id, student_id = "7" * 32, "student-owned"
    own = _intake(exam_id, student_id)
    own_dir = tmp_path / "own"
    foreign_dir = tmp_path / "foreign"
    own_dir.mkdir()
    foreign_dir.mkdir()
    (own_dir / "intake.json").write_text(json.dumps(own))
    (own_dir / "current.json").write_text(json.dumps(own))
    (foreign_dir / "intake.json").write_text(json.dumps(_intake("6" * 32, "student-foreign")))

    with pytest.raises(ArtifactUnavailable):
        read_exam_intake(
            WebSettings(data_dir=tmp_path),
            {"intake_path": "own/current.json", "asset_root": "foreign"},
            exam_id=exam_id,
            student_id=student_id,
        )


@pytest.mark.parametrize(
    "intake_path",
    ["../outside.json", "/outside.json", r"jobs\\exam\\intake.json", "jobs/./intake.json"],
)
def test_rejects_unsafe_pointers(tmp_path, intake_path):
    exam_id = "e" * 32
    asset = tmp_path / "assets"
    asset.mkdir()
    with pytest.raises(ArtifactUnavailable):
        read_exam_intake(
            WebSettings(data_dir=tmp_path),
            {"intake_path": intake_path, "asset_root": "assets"},
            exam_id=exam_id,
            student_id="student-e",
        )


def test_rejects_symlink_escape(tmp_path):
    exam_id, student_id = "f" * 32, "student-f"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "intake.json").write_text(json.dumps(_intake(exam_id, student_id)))
    (tmp_path / "assets").mkdir()
    (tmp_path / "escape.json").symlink_to(outside / "intake.json")
    with pytest.raises(ArtifactUnavailable):
        read_exam_intake(
            WebSettings(data_dir=tmp_path),
            {"intake_path": "escape.json", "asset_root": "assets"},
            exam_id=exam_id,
            student_id=student_id,
        )


def test_api_serves_legacy_page_only_to_exam_owner(tmp_path):
    exam_id = "9" * 32
    job_id = "8" * 32
    database = f"sqlite:///{tmp_path / 'web.sqlite'}"
    root = tmp_path / "private"
    settings = WebSettings(database_url=database, data_dir=root, curriculum_path=tmp_path / "missing.json")
    root.mkdir()
    store = Store(database)
    store.init()
    owner = store.create_user("owner", "synthetic-owner-password", "Owner")
    other = store.create_user("other", "synthetic-other-password", "Other")
    student = store.create_record("student", owner["id"], {"name": "Synthetic student"})
    exam = store.create_record(
        "exam",
        owner["id"],
        {"student_id": student["id"], "question_count": 1, "analysis_path": "analysis.json"},
        record_id=exam_id,
    )
    (root / "analysis.json").write_text("{}")
    intake_relative, asset_relative, intake_dir, run_dir = _legacy_paths(root, job_id)
    image_path = intake_dir / "pages" / "page.png"
    image_path.parent.mkdir()
    Image.new("RGB", (3, 2), "white").save(image_path)
    image_bytes = image_path.read_bytes()
    manifest = _intake(exam_id, student["id"])
    digest = hashlib.sha256(image_bytes).hexdigest()
    manifest["materials"][0]["sha256"] = digest
    manifest["pages"][0]["document_sha256"] = digest
    manifest["pages"][0]["image_sha256"] = digest
    (intake_dir / "intake.json").write_text(json.dumps(manifest))
    (run_dir / "intake-with-regions.json").write_text(json.dumps(manifest))
    record = store.get_record("exam", exam["id"], owner["id"])
    store.update_record(
        "exam",
        exam["id"],
        owner["id"],
        {**record["data"], "intake_path": intake_relative, "asset_root": asset_relative},
        record["revision"],
    )
    app = create_app(settings=settings, store=store)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            owner_session = store.create_session(owner["id"])
            other_session = store.create_session(other["id"])
            url = f"/api/exams/{exam_id}/analysis/pages/page-1/content"
            analysis_url = f"/api/exams/{exam_id}/analysis"
            client.cookies.set(settings.cookie_name, owner_session["token"])
            analysis_result = client.get(analysis_url)
            assert analysis_result.status_code == 200
            assert len(analysis_result.json()["pages"]) == 1
            result = client.get(url)
            assert result.status_code == 200
            assert result.content == image_bytes
            assert str(root) not in result.text
            client.cookies.set(settings.cookie_name, other_session["token"])
            assert client.get(analysis_url).status_code == 404
            assert client.get(url).status_code == 404
    finally:
        store.close()
