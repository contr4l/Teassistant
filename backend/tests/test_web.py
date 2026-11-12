"""Synthetic SQLAlchemy and HTTP boundary tests for the private teacher web app."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from app.prompt_templates import MAX_PROMPT_LENGTH
from app.web_api import create_app
from app.web_settings import WebSettings
from app.web_store import (
    Store,
    StoreConflict,
    StoreNotFound,
    _Job,
    _now,
    _WebSession,
)
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update


def _write_curriculum(path: Path, *, version: str = "synthetic-v1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": version,
                "is_synthetic": True,
                "points": [
                    {"id": "p1", "name": "Synthetic point one"},
                    {"id": "p2", "name": "Synthetic point two", "prerequisite_ids": ["p1"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path: Path, **changes) -> WebSettings:
    values = {
        "database_url": "sqlite://",
        "data_dir": tmp_path / "private",
        "curriculum_path": _write_curriculum(tmp_path / "curriculum.json"),
        "bank_dir": None,
        "semantic_dir": None,
        "frontend_dist": tmp_path / "missing-frontend",
        "allow_codex": False,
        "model_provider": "deepseek",
        "secure_cookies": True,
    }
    values.update(changes)
    return WebSettings(**values)


@pytest.fixture
def web(tmp_path: Path):
    settings = _settings(tmp_path)
    store = Store(settings.database_url, session_hours=settings.session_hours)
    store.init()
    admin = store.create_user("admin", "synthetic-admin-password", "Synthetic Admin", "admin")
    first = store.create_user("teacher-a", "synthetic-teacher-password-a", "Teacher A")
    second = store.create_user("teacher-b", "synthetic-teacher-password-b", "Teacher B")
    app = create_app(settings=settings, store=store)
    yield settings, store, app, admin, first, second
    store.close()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()
    return response.json()


def _csrf(identity: dict[str, str]) -> dict[str, str]:
    return {"X-CSRF-Token": identity["csrf_token"]}


def _png_bytes(color: str = "white") -> bytes:
    image = Image.new("RGB", (3, 2), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_store_password_hash_sessions_and_login_throttle(web) -> None:
    _settings_value, store, _app, admin, _first, _second = web
    assert store.authenticate("ADMIN", "synthetic-admin-password")["id"] == admin["id"]
    for _ in range(8):
        assert store.authenticate("admin", "wrong-password") is None
    assert store.authenticate("admin", "synthetic-admin-password") is None

    session = store.create_session(admin["id"])
    token_hash = hashlib.sha256(session["token"].encode()).hexdigest()
    with store._sessions() as db:
        saved = db.get(_WebSession, token_hash)
        assert saved is not None
        assert session["token"] not in saved.token_hash
        assert saved.csrf_token == session["csrf_token"]
    assert store.lookup_session(session["token"])["user"]["id"] == admin["id"]
    store.delete_session(session["token"])
    assert store.lookup_session(session["token"]) is None


def test_store_records_are_owner_scoped_and_use_revision_cas(web) -> None:
    _settings_value, store, _app, _admin, first, second = web
    record = store.create_record("student", first["id"], {"name": "Synthetic student"})
    assert store.get_record("student", record["id"], second["id"]) is None
    with pytest.raises(StoreNotFound):
        store.update_record("student", record["id"], second["id"], {"name": "tamper"}, 1)
    updated = store.update_record("student", record["id"], first["id"], {"name": "Changed"}, 1)
    assert updated["revision"] == 2
    with pytest.raises(StoreConflict):
        store.update_record("student", record["id"], first["id"], {"name": "stale"}, 1)


def test_store_job_idempotency_expired_lease_and_old_worker_cas(web) -> None:
    _settings_value, store, _app, _admin, teacher, _second = web
    first = store.enqueue_job(teacher["id"], "analysis", "exam-x", {"snapshot": 1}, "idem-x")
    duplicate = store.enqueue_job(teacher["id"], "analysis", "exam-x", {"snapshot": 1}, "idem-x")
    assert duplicate["id"] == first["id"]
    with pytest.raises(StoreConflict):
        store.enqueue_job(teacher["id"], "analysis", "exam-x", {"snapshot": 2}, "idem-x")
    assert store.get_job(first["id"], owner_id="someone-else") is None

    claimed = store.claim_job("worker-old", lease_seconds=60)
    assert claimed["status"] == "running" and claimed["attempts"] == 1
    with store.engine.begin() as connection:
        connection.execute(
            update(_Job)
            .where(_Job.id == claimed["id"])
            .values(lease_expires_at=_now() - timedelta(seconds=1))
        )
    assert not store.finish_job(claimed["id"], claimed["lease_token"], {"stale": True})
    reclaimed = store.claim_job("worker-new", lease_seconds=60)
    assert reclaimed["id"] == claimed["id"]
    assert reclaimed["lease_token"] != claimed["lease_token"]
    assert reclaimed["attempts"] == 2
    assert not store.finish_job(claimed["id"], claimed["lease_token"], {"old": True})
    assert store.finish_job(reclaimed["id"], reclaimed["lease_token"], {"ok": True})


def test_finish_job_guards_and_resource_update_are_atomic(web) -> None:
    _settings_value, store, _app, _admin, teacher, _second = web
    student = store.create_record("student", teacher["id"], {"progress": 1})
    exam = store.create_record("exam", teacher["id"], {"analysis_path": None})
    job = store.enqueue_job(teacher["id"], "analysis", exam["id"], {"frozen": True})
    claimed = store.claim_job("worker", lease_seconds=60)
    assert claimed["id"] == job["id"]
    student = store.update_record("student", student["id"], teacher["id"], {"progress": 2}, 1)

    publication = {
        "kind": "exam",
        "record_id": exam["id"],
        "owner_id": teacher["id"],
        "data": {"analysis_path": "jobs/synthetic/analysis.json"},
        "expected_revision": exam["revision"],
    }
    stale_guard = [
        {"kind": "student", "record_id": student["id"], "owner_id": teacher["id"], "revision": 1}
    ]
    assert not store.finish_job(
        claimed["id"],
        claimed["lease_token"],
        {"analysis_revision": "r1"},
        resource_update=publication,
        guards=stale_guard,
    )
    assert store.get_record("exam", exam["id"], teacher["id"])["data"]["analysis_path"] is None
    assert store.get_job(job["id"], teacher["id"])["status"] == "running"

    valid_guard = [
        {"kind": "student", "record_id": student["id"], "owner_id": teacher["id"], "revision": 2}
    ]
    assert store.finish_job(
        claimed["id"],
        claimed["lease_token"],
        {"analysis_revision": "r1"},
        resource_update=publication,
        guards=valid_guard,
    )
    assert store.get_record("exam", exam["id"], teacher["id"])["revision"] == 2
    assert store.get_job(job["id"], teacher["id"])["status"] == "completed"


def test_prompt_history_activation_and_submission_snapshot_are_immutable(web) -> None:
    _settings_value, store, _app, admin, _first, _second = web
    store.ensure_prompts({"paper": {"label": "Paper", "description": "Synthetic", "body": "v1"}})
    original_snapshot = store.snapshot_prompts()
    saved = store.save_prompt("paper", "v2 <safe text>", admin["id"], expected_revision=1)
    assert saved["active_revision"] == 2
    with pytest.raises(StoreConflict):
        store.save_prompt("paper", "stale", admin["id"], expected_revision=1)
    activated = store.activate_prompt(
        "paper", revision=1, created_by=admin["id"], expected_revision=2
    )
    assert activated["active_revision"] == 3 and activated["body"] == "v1"
    assert store.snapshot_prompts()["paper"]["revision"] == 3
    assert original_snapshot["paper"]["revision"] == 1
    assert [row["revision"] for row in store.prompt_history("paper")] == [1, 2, 3]
    with pytest.raises(ValueError, match="too large"):
        store.save_prompt("paper", "x" * (MAX_PROMPT_LENGTH + 1), admin["id"], 3)


def test_http_auth_csrf_admin_prompts_and_tenant_isolation(web) -> None:
    settings, store, app, _admin, first, second = web
    with (
        TestClient(app, base_url="https://testserver") as client_a,
        TestClient(app, base_url="https://testserver") as client_b,
    ):
        a = _login(client_a, first["username"], "synthetic-teacher-password-a")
        b = _login(client_b, second["username"], "synthetic-teacher-password-b")
        assert client_a.get("/api/auth/me").json()["user"]["id"] == first["id"]
        assert (
            client_a.post("/api/students", json={"name": "No CSRF", "grade": 7}).status_code == 403
        )
        created = client_a.post(
            "/api/students",
            headers=_csrf(a),
            json={
                "name": "Synthetic learner",
                "school": "Demo",
                "grade": 7,
                "learned_point_ids": ["p1"],
            },
        )
        assert created.status_code == 201, created.text
        student_id = created.json()["id"]
        assert client_b.get(f"/api/students/{student_id}").status_code == 404
        assert client_b.get(f"/api/students/{student_id}/exams").status_code == 404
        assert client_b.get(f"/api/students/{student_id}/practices").status_code == 404
        patched_null = client_a.patch(
            f"/api/students/{student_id}",
            headers=_csrf(a),
            json={"expected_revision": 1, "learned_point_ids": None},
        )
        assert patched_null.status_code == 422

        exam = client_a.post(
            "/api/exams",
            headers=_csrf(a),
            json={"student_id": student_id, "title": "Synthetic exam", "question_count": 2},
        )
        assert exam.status_code == 201
        exam_id = exam.json()["id"]
        assert client_b.get(f"/api/exams/{exam_id}").status_code == 404
        assert (
            client_b.post(
                "/api/exams",
                headers=_csrf(b),
                json={"student_id": student_id, "title": "Cross-owner", "question_count": 1},
            ).status_code
            == 404
        )

        uploaded = client_a.post(
            f"/api/exams/{exam_id}/assets",
            data={"role": "paper"},
            files={"file": ("../../proof.pdf", _png_bytes(), "application/pdf")},
            headers=_csrf(a),
        )
        assert uploaded.status_code == 201, uploaded.text
        asset = uploaded.json()
        assert asset["mime"] == "image/png" and asset["filename"] == "proof.pdf"
        assert "path" not in asset and "sha256" not in asset
        assert client_b.get(f"/api/assets/{asset['id']}/content").status_code == 404
        content = client_a.get(f"/api/assets/{asset['id']}/content")
        assert content.status_code == 200 and content.content == _png_bytes()

        order = client_a.post(
            f"/api/exams/{exam_id}/assets/order",
            headers=_csrf(a),
            json={"asset_ids": [asset["id"]]},
        )
        assert order.status_code == 200
        student_view = client_a.get(f"/api/students/{student_id}").json()
        assert student_view["progress_revision"] == 1

        assert client_b.get("/api/jobs/" + ("f" * 32)).status_code == 404
        logout = client_a.post("/api/auth/logout", headers=_csrf(a))
        assert logout.status_code == 204
        assert client_a.get("/api/auth/me").status_code == 401


def test_http_password_preserves_store_and_admin_created_boundary_spaces(web) -> None:
    _settings_value, store, app, admin, _first, _second = web
    spaced_password = "  synthetic pass  "
    stored = store.create_user("space-store", spaced_password, "Store Spaced User")
    with TestClient(app, base_url="https://testserver") as client:
        exact = _login(client, stored["username"], spaced_password)
        assert exact["user"]["id"] == stored["id"]
        trimmed = client.post(
            "/api/auth/login",
            json={"username": stored["username"], "password": spaced_password.strip()},
        )
        assert trimmed.status_code == 401

        admin_client = TestClient(app, base_url="https://testserver")
        try:
            admin_session = _login(admin_client, admin["username"], "synthetic-admin-password")
            created = admin_client.post(
                "/api/admin/users",
                headers=_csrf(admin_session),
                json={
                    "username": "space-api",
                    "display_name": "API Spaced User",
                    "password": spaced_password,
                    "role": "teacher",
                },
            )
            assert created.status_code == 201, created.text
        finally:
            admin_client.close()

        created_login = client.post(
            "/api/auth/login",
            json={"username": "space-api", "password": spaced_password},
        )
        assert created_login.status_code == 200
        rejected_trimmed = client.post(
            "/api/auth/login",
            json={"username": "space-api", "password": spaced_password.strip()},
        )
        assert rejected_trimmed.status_code == 401


def test_http_admin_prompt_revision_and_teacher_cannot_edit(web) -> None:
    _settings_value, _store, app, admin, teacher, _second = web
    with (
        TestClient(app, base_url="https://testserver") as admin_client,
        TestClient(app, base_url="https://testserver") as teacher_client,
    ):
        admin_session = _login(admin_client, admin["username"], "synthetic-admin-password")
        teacher_session = _login(
            teacher_client, teacher["username"], "synthetic-teacher-password-a"
        )
        added = admin_client.post(
            "/api/admin/users",
            headers=_csrf(admin_session),
            json={
                "username": "new-teacher",
                "display_name": "Synthetic New Teacher",
                "password": "synthetic-new-teacher-password",
                "role": "teacher",
            },
        )
        assert added.status_code == 201
        assert added.json()["role"] == "teacher" and "password" not in added.json()
        assert any(
            row["id"] == added.json()["id"] for row in admin_client.get("/api/admin/users").json()
        )
        assert teacher_client.get("/api/admin/users").status_code == 403
        prompts = admin_client.get("/api/admin/prompts")
        assert prompts.status_code == 200 and {row["key"] for row in prompts.json()} == {
            "layout",
            "paper",
            "reference",
            "response",
            "diagnosis",
            "report",
        }
        assert teacher_client.get("/api/admin/prompts").status_code == 403
        assert (
            teacher_client.post(
                "/api/admin/prompts/paper",
                headers=_csrf(teacher_session),
                json={"body": "<script>synthetic</script>", "expected_revision": 1},
            ).status_code
            == 403
        )

        saved = admin_client.post(
            "/api/admin/prompts/paper",
            headers=_csrf(admin_session),
            json={"body": "<script>synthetic but JSON only</script>", "expected_revision": 1},
        )
        assert saved.status_code == 200 and saved.json()["active_revision"] == 2
        assert saved.headers.get("content-type", "").startswith("application/json")
        history = admin_client.get("/api/admin/prompts/paper/history").json()
        assert [entry["revision"] for entry in history] == [1, 2]
        stale = admin_client.post(
            "/api/admin/prompts/paper",
            headers=_csrf(admin_session),
            json={"body": "stale", "expected_revision": 1},
        )
        assert stale.status_code == 409
        too_long = admin_client.post(
            "/api/admin/prompts/paper",
            headers=_csrf(admin_session),
            json={"body": "x" * (MAX_PROMPT_LENGTH + 1), "expected_revision": 2},
        )
        assert too_long.status_code == 422
        reverted = admin_client.post(
            "/api/admin/prompts/paper/activate",
            headers=_csrf(admin_session),
            json={"revision": 1, "expected_revision": 2},
        )
        assert reverted.status_code == 200 and reverted.json()["active_revision"] == 3


def test_practice_feedback_is_reloaded_and_private_file_paths_are_never_listed(web) -> None:
    settings, store, app, _admin, teacher, _second = web
    student = store.create_record(
        "student",
        teacher["id"],
        {"name": "Synthetic student", "school": "", "grade": 7, "learned_point_ids": ["p1"]},
    )
    private = Path(settings.data_dir) / "jobs" / "synthetic" / "student.pdf"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_bytes(b"synthetic PDF bytes")
    digest = hashlib.sha256(private.read_bytes()).hexdigest()
    practice = store.create_record(
        "practice",
        teacher["id"],
        {
            "student_id": student["id"],
            "progress_revision": "1",
            "status": "completed",
            "snapshot": {"is_synthetic": True},
            "files": [
                {
                    "id": "student_pdf",
                    "name": "student.pdf",
                    "label": "Student",
                    "path": "jobs/synthetic/student.pdf",
                    "sha256": digest,
                    "filename": "student.pdf",
                }
            ],
            "warnings": [],
            "preview": None,
            "feedback": None,
        },
        parent_id=student["id"],
    )
    with TestClient(app, base_url="https://testserver") as client:
        session = _login(client, teacher["username"], "synthetic-teacher-password-a")
        listed = client.get(f"/api/students/{student['id']}/practices").json()
        assert listed[0]["files"] == [
            {
                "id": "student_pdf",
                "label": "Student",
                "url": f"/api/practices/{practice['id']}/files/student.pdf",
            }
        ]
        assert "path" not in json.dumps(listed)
        feedback = client.post(
            f"/api/practices/{practice['id']}/feedback",
            headers=_csrf(session),
            json={
                "expectation": "yes",
                "quality": 5,
                "saved_minutes": 20,
                "reasons": ["useful"],
                "comment": "synthetic feedback",
            },
        )
        assert feedback.status_code == 200
        fetched = client.get(f"/api/practices/{practice['id']}").json()
        assert fetched["feedback"]["comment"] == "synthetic feedback"
        downloaded = client.get(fetched["files"][0]["url"])
        assert downloaded.status_code == 200 and downloaded.content == b"synthetic PDF bytes"
        student_before = store.get_record("student", student["id"], teacher["id"])
        changed = store.update_record(
            "student",
            student["id"],
            teacher["id"],
            {**student_before["data"], "learned_point_ids": []},
            expected_revision=student_before["revision"],
        )
        assert changed["revision"] == 2
        stale_view = client.get(f"/api/practices/{practice['id']}").json()
        assert stale_view["files"] == []
        assert any("教学进度已变化" in item for item in stale_view["warnings"])
        assert client.get(fetched["files"][0]["url"]).status_code == 409
        assert (
            client.get(f"/api/practices/{practice['id']}/files/../student.pdf").status_code == 404
        )


def test_frontend_fallback_does_not_swallow_unknown_api_or_private_paths(
    web, tmp_path: Path
) -> None:
    settings, store, _app, _admin, _first, _second = web
    dist = tmp_path / "frontend-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>synthetic app</body></html>", encoding="utf-8")
    app = create_app(settings=replace(settings, frontend_dist=dist), store=store)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/students/example").text == "<html><body>synthetic app</body></html>"
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/private/secret.json").status_code == 404
        assert client.get("/work/private/report.json").status_code == 404


def test_practice_engine_is_explicit_and_api_preserves_worker_request_version(
    web, monkeypatch, tmp_path: Path
) -> None:
    settings, store, _app, _admin, teacher, _second = web
    student = store.create_record(
        "student",
        teacher["id"],
        {"name": "Synthetic student", "school": "", "grade": 7, "learned_point_ids": ["p1"]},
    )
    captured: dict[str, object] = {}

    def fake_job_input(_store, _settings, kind, resource):
        captured["kind"] = kind
        captured["resource"] = resource
        return {
            "request": {"difficulty_version": "synthetic-semantic-v1"},
            "progress_revision": str(resource["revision"]),
            "prompt_snapshot": {"paper": {"revision": 4, "body": "synthetic prompt"}},
            "resource_data": resource["data"],
        }

    monkeypatch.setattr("app.web_api._job_input", fake_job_input)
    settings = replace(settings, bank_dir=tmp_path / "bank", semantic_dir=tmp_path / "semantic")
    app = create_app(settings=settings, store=store)
    with TestClient(app, base_url="https://testserver") as client:
        session = _login(client, teacher["username"], "synthetic-teacher-password-a")
        response = client.post(
            "/api/practices",
            headers=_csrf(session),
            json={
                "student_id": student["id"],
                "exam_ids": [],
                "quotas": [{"point_id": "p1", "count": 1}],
                "seed": 7,
                "layout": "answer_space",
                "pdf_engine": "word",
            },
        )
        assert response.status_code == 202, response.text
        practice = store.get_record("practice", response.json()["id"], teacher["id"])
        assert practice["data"]["pdf_engine"] == "word"
        job = store.get_job(response.json()["job_id"], teacher["id"])
        assert job["input_data"]["request"]["difficulty_version"] == "synthetic-semantic-v1"
        assert job["input_data"]["resource_data"]["pdf_engine"] == "word"
        assert captured["kind"] == "practice"
