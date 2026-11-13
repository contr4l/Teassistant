"""Synthetic administrator changes exercise the real teacher selection path."""

import copy
import hashlib
import json
from dataclasses import replace
from io import BytesIO

import httpx
import pytest
from app import admin_bank, web_jobs
from app import admin_bank_ingest as ingestion
from app.bank_practice_contracts import BankPracticeSnapshot
from app.model_billing import BillingStore
from app.model_settings import ModelSettingsStore
from app.semantic_bank import canonical_hash
from app.web_api import create_app
from app.web_store import StoreConflict
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from test_web_jobs import setup


def _patch(revision=1, **kwargs):
    return {"expected_revision": revision, "enabled": True, "point_ids": ["p0"],
            "prerequisite_ids": [], "difficulty_score": 86,
            "reason": "合成：调整教学标签", "note": "合成备注", **kwargs}


def test_adjustments_change_inventory_and_selection_but_never_ai_or_source(tmp_path):
    store, settings, teacher, student, practice = setup(tmp_path)
    administrator = store.create_user("synthetic-admin", "synthetic-pw", "合成管理员", "admin")
    before = admin_bank.load_bank(store, settings)
    question = before.questions[0]
    source_before = (settings.bank_dir / "bank.json").read_bytes()
    annotations_before = (settings.semantic_dir / "annotations.json").read_bytes()
    admin_bank.save_adjustment(store, settings, question.id, _patch(), administrator["id"])
    after = admin_bank.load_bank(store, settings)
    adjusted = after.questions[0]
    assert adjusted.status == "eligible"
    assert adjusted.point_ids == ["p0"] and adjusted.difficulty == "advanced"
    assert adjusted.semantic_payload == question.semantic_payload
    assert adjusted.source_payload == question.source_payload
    assert (settings.bank_dir / "bank.json").read_bytes() == source_before
    assert (settings.semantic_dir / "annotations.json").read_bytes() == annotations_before
    inventory = web_jobs.get_inventory(store, settings, teacher["id"], student["id"])
    assert inventory["points"][0]["by_difficulty"]["advanced"] == 1
    assert inventory["points"][1]["candidate_count"] == 0
    payload = copy.deepcopy(practice["data"])
    payload["quotas"] = [{"point_id": "p0", "count": 2}]
    practice = store.update_record("practice", practice["id"], teacher["id"], payload, 1)
    frozen = web_jobs.build_job_input(store, settings, "practice", practice)
    queued = store.enqueue_job(teacher["id"], "practice", practice["id"], frozen)
    assert web_jobs.run_once(store, settings, "synthetic-worker")
    assert store.get_job(queued["id"])["status"] == "partial"
    completed = store.get_record("practice", practice["id"], teacher["id"])
    snapshot = BankPracticeSnapshot.model_validate(completed["data"]["snapshot"])
    assert snapshot.items[0].question.admin_adjustment.source == "admin_override"
    assert snapshot.items[0].question.point_ids == ["p0"]


def test_disabling_and_restore_never_mutate_previous_snapshot(tmp_path):
    store, settings, teacher, student, _practice = setup(tmp_path)
    before = admin_bank.load_bank(store, settings)
    question = before.questions[0]
    changed = admin_bank.save_adjustment(store, settings, question.id,
                                         _patch(enabled=False), teacher["id"])
    assert changed["status"] == "blocked" and changed["disabled"]
    assert admin_bank.summary(store, settings)["disabled_count"] == 1
    assert web_jobs.get_inventory(store, settings, teacher["id"], student["id"])[
        "eligible_within_progress"] == 0
    admin_bank.restore(store, settings, "r1", 2, teacher["id"], "合成：恢复旧版")
    restored = admin_bank.load_bank(store, settings)
    assert restored == before
    assert admin_bank.summary(store, settings)["revision"] == 3
    with pytest.raises(StoreConflict):
        admin_bank.save_adjustment(store, settings, question.id, _patch(), teacher["id"])


@pytest.mark.parametrize("legacy_input", [False, True])
def test_bank_change_after_enqueue_invalidates_old_selection(tmp_path, legacy_input):
    store, settings, teacher, _student, practice = setup(tmp_path)
    frozen = web_jobs.build_job_input(store, settings, "practice", practice)
    if legacy_input:
        frozen.pop("bank_revision")
    queued = store.enqueue_job(teacher["id"], "practice", practice["id"], frozen)
    qid = admin_bank.load_bank(store, settings).questions[0].id
    admin_bank.save_adjustment(store, settings, qid, _patch(), teacher["id"])
    web_jobs.run_once(store, settings, "synthetic-worker")
    result = store.get_job(queued["id"])
    assert result["status"] == "failed" and "题库" in result["error"]
    assert "snapshot" not in store.get_record("practice", practice["id"], teacher["id"])["data"]


def test_http_upload_is_persistent_private_and_owner_bound(tmp_path):
    store, settings, teacher, _student, _practice = setup(tmp_path)
    admin = store.create_user("synthetic-admin", "synthetic-pw", "合成管理员", "admin")
    session = store.create_session(admin["id"])
    headers = {"X-CSRF-Token": session["csrf_token"]}
    buffer = BytesIO()
    document = Document()
    document.add_paragraph("1. 合成题目")
    document.save(buffer)
    raw = buffer.getvalue()
    with TestClient(create_app(settings, store=store)) as client:
        client.cookies.set(settings.cookie_name, session["token"])
        initial = client.get("/api/admin/bank").json()
        assert initial["question_count"] == 1
        files = [("files", (name, raw)) for name in (
            "合成学校数学试卷（原卷版）.docx", "合成学校数学试卷（解析版）.docx")]
        response = client.post("/api/admin/bank/imports", headers=headers, files=files,
                               data={"expected_revision": "1", "reason": "合成导入"})
        assert response.status_code == 202, response.text
        job = store.get_job(response.json()["id"], admin["id"])
        assert job["kind"] == "bank_import"
        for file in job["input_data"]["files"]:
            path = settings.data_dir / file["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == file["sha256"]
            assert path.stat().st_mode & 0o077 == 0
        teacher_session = store.create_session(teacher["id"])
        client.cookies.set(settings.cookie_name, teacher_session["token"])
        assert client.get("/api/jobs/" + job["id"]).status_code == 404
        client.cookies.set(settings.cookie_name, session["token"])
        assert client.get("/api/admin/bank").json()["imports"][0]["job"]["id"] == job["id"]


def test_first_import_can_publish_to_empty_seed_and_restores_shared_state(tmp_path):
    # Package generation is exercised separately by the actual ingest tests.
    package_question_bank = ingestion._load_script_module("package_question_bank").package_question_bank

    store, settings, teacher, _student, _practice = setup(tmp_path)
    path = settings.data_dir / "packages" / "synthetic-package"
    package_question_bank(settings.bank_dir, settings.semantic_dir, path)
    empty = replace(settings, bank_dir=None, semantic_dir=None)
    assert not admin_bank.summary(store, empty)["configured"]
    publication = admin_bank.prepare_import_publication(
        store, empty, path.relative_to(settings.data_dir).as_posix(), 1,
        teacher["id"], "合成初次导入",
    )
    store.update_record(publication["kind"], publication["record_id"], publication["owner_id"],
                        publication["data"], publication["expected_revision"])
    assert admin_bank.summary(store, empty)["eligible_count"] == 1
    assert admin_bank.effective_settings(store, empty).bank_dir == path / "source"
    assert json.loads((path / "source/bank.json").read_bytes())["schema_version"] == "source-bank-v3"


@pytest.mark.parametrize("malformed", [False, True])
def test_http_to_real_worker_import_publication_inventory_and_billing(tmp_path, monkeypatch, malformed):
    store, seeded, teacher, student, _practice = setup(tmp_path)
    settings = replace(seeded, bank_dir=None, semantic_dir=None)
    admin = store.create_user("synthetic-pipeline-admin", "synthetic-pw", "合成管理员", "admin")
    ModelSettingsStore(settings.data_dir).save(
        expected_revision=0, api_key="SYNTHETIC_KEY_NOT_REAL", model="deepseek-flash",
        prices={"input_cache_hit": "0.04", "input_cache_miss": "2", "output": "8"},
        actor_id=admin["id"],
    )
    session = store.create_session(admin["id"])

    def render(bank_dir, output_dir, question_ids, **_kwargs):
        output_dir.mkdir()
        image_path = output_dir / "synthetic.png"
        Image.new("RGB", (2, 2), "white").save(image_path)
        rows = json.loads((bank_dir / "bank.json").read_bytes())["questions"]
        manifest = {"questions": [{
            "question_id": row["id"], "source_hash": canonical_hash(row),
            "association_status": row["association_status"], "reliable_solution_used": True,
            "text": "SYNTHETIC rendered test replacement",
            "images": [{"path": "synthetic.png", "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}],
        } for row in rows]}
        (output_dir / "inputs.json").write_text(json.dumps(manifest))
        return manifest

    def provider(request):
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"][0]["text"]
        entries = json.loads(prompt.split("题目数据：", 1)[1])
        content = "invalid-json" if malformed else json.dumps({"assessments": [{
            "question_id": item["question_id"], "status": "assessed", "point_ids": ["p1"],
            "prerequisite_ids": [], "difficulty_score": 40, "reasoning": "synthetic assessment",
            "point_evidence": [{"point_id": "p1", "role": "target", "reason": "synthetic evidence"}],
            "warnings": [],
        } for item in entries]})
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    assess = ingestion._assess_batches

    async def synthetic_assess(**kwargs):
        return await assess(**kwargs, network_factory=lambda: httpx.MockTransport(provider))

    monkeypatch.setattr(ingestion, "render_inputs", render)
    monkeypatch.setattr(ingestion, "_assess_batches", synthetic_assess)
    uploads = []
    for name, lines in (
        ("合成数学考试（原卷版）.docx", ["一、解答题", "1. 求一次函数的值"]),
        ("合成数学考试（解析版）.docx", ["一、解答题", "1. 求一次函数的值", "【答案】2", "【解析】代入计算"]),
    ):
        document = Document()
        for line in lines:
            document.add_paragraph(line)
        stream = BytesIO()
        document.save(stream)
        uploads.append(("files", (name, stream.getvalue())))
    with TestClient(create_app(settings, store=store)) as client:
        client.cookies.set(settings.cookie_name, session["token"])
        response = client.post("/api/admin/bank/imports", files=uploads,
                               headers={"X-CSRF-Token": session["csrf_token"]},
                               data={"expected_revision": "1", "reason": "合成全链路更新"})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert web_jobs.run_once(store, settings, "synthetic-independent-worker")
        job = client.get("/api/jobs/" + job_id).json()
        assert job["status"] == ("failed" if malformed else "completed"), job
        summary = client.get("/api/admin/bank").json()
        assert summary["configured"] is (not malformed)
        assert summary["revision"] == (1 if malformed else 2)
        assert "package_path" not in json.dumps(job)
        teacher_session = store.create_session(teacher["id"])
        client.cookies.set(settings.cookie_name, teacher_session["token"])
        assert client.get("/api/jobs/" + job_id).status_code == 404
        if not malformed:
            inventory = client.get("/api/inventory", params={"student_id": student["id"]})
            assert inventory.status_code == 200, inventory.text
            assert inventory.json()["eligible_within_progress"] == 1
            assert client.get("/api/capabilities").json()["bank_configured"]
    wallet = BillingStore(store.engine).get_wallet(admin["id"])
    assert wallet["used_credit"] != "0.000000"
    assert wallet["reserved_credit"] == "0.000000"
