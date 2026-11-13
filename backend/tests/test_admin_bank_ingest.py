"""Synthetic-only checks for administrator source-bank merging."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from app.admin_bank_ingest import (
    AdminBankImportError,
    _assess_batches,
    _load_script_module,
    _merge_sources,
    process_import,
)
from app.bank_practice import load_practice_bank
from app.contracts import Curriculum, KnowledgePoint
from app.model_billing import BillingStore
from app.model_settings import ModelSettingsError, ModelSettingsStore
from app.semantic_bank import canonical_hash
from app.web_settings import WebSettings
from app.web_store import Store
from PIL import Image
from test_docx_ingest import fixture_docs


def _plain_docx(path: Path, paragraphs: list[str]) -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in paragraphs)
    document = f'<w:document xmlns:w="{namespace}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)


def test_normalized_pair_role_replaces_only_matching_original(tmp_path):
    existing, incoming = tmp_path / "existing", tmp_path / "incoming"
    (existing / "sources").mkdir(parents=True)
    incoming.mkdir()
    old_original, old_analysis = fixture_docs(existing)
    shutil.move(old_original, existing / "sources" / old_original.name)
    shutil.move(old_analysis, existing / "sources" / old_analysis.name)
    old_original = existing / "sources" / old_original.name
    old_analysis = existing / "sources" / old_analysis.name
    # The helper creates a matched pair under one shared synthetic paper name.
    replacement = incoming / "Synthetic Exam.docx"
    shutil.copyfile(old_original, replacement)
    destination = tmp_path / "merged"

    count = _merge_sources(existing, [replacement], destination)

    assert count == 2
    assert (destination / replacement.name).read_bytes() == replacement.read_bytes()
    assert (destination / old_analysis.name).read_bytes() == old_analysis.read_bytes()
    assert old_original.is_file() and old_analysis.is_file()


def test_conflicting_normalized_pair_role_is_rejected_without_partial_merge(tmp_path):
    existing = tmp_path / "existing"
    (existing / "sources").mkdir(parents=True)
    original, analysis = fixture_docs(existing)
    shutil.move(original, existing / "sources" / original.name)
    shutil.move(analysis, existing / "sources" / analysis.name)
    original = existing / "sources" / original.name
    incoming = tmp_path / "Synthetic Exam (原卷版).docx"
    incoming.write_bytes(original.read_bytes() + b"different synthetic bytes")
    destination = tmp_path / "merged"

    with pytest.raises(AdminBankImportError, match="歧义试卷角色"):
        _merge_sources(existing, [original, incoming], destination)

    assert not any(destination.iterdir())


def test_initial_import_requires_a_complete_original_and_analysis_pair(tmp_path):
    original, analysis = fixture_docs(tmp_path)
    target = tmp_path / "merged"
    assert _merge_sources(None, [original, analysis], target) == 2
    assert {path.name for path in target.glob("*.docx")} == {original.name, analysis.name}

    incomplete = tmp_path / "incomplete"
    with pytest.raises(AdminBankImportError, match="原卷和解析"):
        _merge_sources(None, [original], incomplete)
    assert not any(incomplete.iterdir())


def test_script_loader_works_without_repository_root_on_sys_path(monkeypatch):
    backend = str(__import__("app").__path__[0].rsplit("/app", 1)[0])
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != str(Path(__file__).resolve().parents[2])])
    assert backend in sys.path
    module = _load_script_module("build_question_bank")
    assert callable(module.build)


@pytest.mark.parametrize("mode", ["valid", "malformed", "missing_key"])
def test_semantic_batch_uses_synthetic_mock_transport_and_real_job_billing(
    tmp_path, mode
):
    qid = "synthetic-q1"
    question = {"id": qid, "association_status": "confirmed"}
    bank, inputs, run = tmp_path / "bank", tmp_path / "inputs", tmp_path / "semantic-run"
    bank.mkdir()
    (inputs / "images").mkdir(parents=True)
    (bank / "bank.json").write_text(json.dumps({"questions": [question]}), encoding="utf-8")
    image_buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_buffer, format="PNG")
    image_path = inputs / "images/question.png"
    image_path.write_bytes(image_buffer.getvalue())
    input_row = {
        "question_id": qid,
        "source_hash": canonical_hash(question),
        "association_status": "confirmed",
        "reliable_solution_used": True,
        "text": "SYNTHETIC test question",
        "images": [{"path": "images/question.png", "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}],
    }
    (inputs / "inputs.json").write_text(json.dumps({"questions": [input_row]}), encoding="utf-8")
    curriculum = Curriculum(
        version="synthetic-v1",
        is_synthetic=True,
        points=[KnowledgePoint(id="p1", name="synthetic point", prerequisite_ids=[])],
    )
    curriculum_path = tmp_path / "curriculum.json"
    curriculum_path.write_text(curriculum.model_dump_json(), encoding="utf-8")
    assessment = {
        "question_id": qid,
        "status": "assessed",
        "point_ids": ["p1"],
        "prerequisite_ids": [],
        "difficulty_score": 50,
        "reasoning": "synthetic test result",
        "point_evidence": [{"point_id": "p1", "role": "target", "reason": "synthetic evidence"}],
        "warnings": [],
    }

    def mock_response(request):
        assert json.loads(request.content)["model"] == "deepseek-flash"
        content = "not-json" if mode == "malformed" else json.dumps({"assessments": [assessment]})
        body = {
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        return httpx.Response(200, json=body)

    database_path = tmp_path / "billing.sqlite"
    private_data = tmp_path / "private"
    settings = WebSettings(
        database_url=f"sqlite:///{database_path}",
        data_dir=private_data,
        curriculum_path=curriculum_path,
    )
    store = Store(settings.database_url)
    store.init()
    billing = BillingStore(store.engine)
    billing.init()
    billing.save_defaults("0", 1, "synthetic-fixture-admin")
    user = store.create_user("synthetic-admin", "synthetic-password", "合成管理员")
    pending = store.enqueue_job(user["id"], "analysis", "synthetic-resource", {})
    job = store.claim_job("synthetic-worker")
    assert job["id"] == pending["id"]
    job["input_data"] = {"curriculum_sha256": hashlib.sha256(curriculum_path.read_bytes()).hexdigest()}
    if mode != "missing_key":
        ModelSettingsStore(settings.data_dir).save(
            expected_revision=0,
            api_key="SYNTHETIC_KEY_NOT_REAL",
            model="deepseek-flash",
            prices={"input_cache_hit": "0.04", "input_cache_miss": "2", "output": "8"},
            actor_id="synthetic-admin",
        )
        billing.top_up(user["id"], "3", "synthetic-import-credit", "synthetic-admin")
    def invoke():
        return asyncio.run(
            _assess_batches(
            store=store,
            settings=settings,
            job=job,
            curriculum=curriculum,
            bank_root=bank,
            inputs_dir=inputs,
            run_dir=run,
            network_factory=lambda: httpx.MockTransport(mock_response),
            )
        )

    if mode == "malformed":
        with pytest.raises(AdminBankImportError, match="全部新题库题目"):
            invoke()
    elif mode == "missing_key":
        with pytest.raises(ModelSettingsError, match="尚未配置"):
            invoke()
        assert not run.exists()
        assert billing.get_wallet(user["id"])["reserved_credit"] == "0.000000"
        store.close()
        return
    else:
        metadata = invoke()
        annotation = json.loads((run / "annotations.json").read_bytes())["questions"][qid]
        assert metadata["source_bank_sha256"] == hashlib.sha256((bank / "bank.json").read_bytes()).hexdigest()
        assert annotation["provenance"]["result_sha256"] == hashlib.sha256((run / "calls/0001/result.json").read_bytes()).hexdigest()
    call_result = json.loads((run / "calls/0001/result.json").read_bytes())
    assert call_result["synthetic"] is True
    assert ("not-json" in call_result["raw_response"]) if mode == "malformed" else "synthetic test result" in call_result["raw_response"]
    wallet = billing.get_wallet(user["id"])
    assert wallet["used_credit"] != "0.000000"
    assert wallet["reserved_credit"] == "0.000000"
    store.close()


def test_first_import_builds_and_loads_complete_practice_package(tmp_path, monkeypatch):
    from app import admin_bank_ingest

    private_data = tmp_path / "private"
    curriculum_path = tmp_path / "curriculum.json"
    curriculum = Curriculum(
        version="synthetic-v1",
        is_synthetic=True,
        points=[KnowledgePoint(id="p1", name="synthetic point", prerequisite_ids=[])],
    )
    curriculum_path.write_text(curriculum.model_dump_json(), encoding="utf-8")
    settings = WebSettings(
        database_url=f"sqlite:///{tmp_path / 'pipeline.sqlite'}",
        data_dir=private_data,
        curriculum_path=curriculum_path,
    )
    store = Store(settings.database_url)
    store.init()
    billing = BillingStore(store.engine)
    billing.init()
    billing.save_defaults("0", 1, "synthetic-fixture-admin")
    user = store.create_user("pipeline-admin", "synthetic-password", "合成管理员")
    pending = store.enqueue_job(user["id"], "analysis", "synthetic-resource", {})
    job = store.claim_job("synthetic-worker")
    assert job["id"] == pending["id"]
    job["kind"] = "bank_import"

    upload_dir = private_data / "uploads"
    upload_dir.mkdir(parents=True)
    documents_root = tmp_path / "documents"
    documents_root.mkdir()
    original = documents_root / "Synthetic Paper（原卷版）.docx"
    analysis = documents_root / "精品解析：Synthetic Paper（解析版）.docx"
    _plain_docx(original, ["一、解答题", "1. 求一次函数的值"])
    _plain_docx(analysis, ["一、解答题", "1. 求一次函数的值", "【答案】2", "【解析】代入计算"])
    upload_files = []
    for source in (original, analysis):
        target = upload_dir / source.name
        shutil.copyfile(source, target)
        upload_files.append(
            {
                "filename": target.name,
                "path": target.relative_to(private_data).as_posix(),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    job["input_data"] = {
        "files": upload_files,
        "curriculum_sha256": hashlib.sha256(curriculum_path.read_bytes()).hexdigest(),
        "bank_revision": 0,
        "reason": "synthetic import test",
    }
    ModelSettingsStore(private_data).save(
        expected_revision=0,
        api_key="SYNTHETIC_KEY_NOT_REAL",
        model="deepseek-flash",
        prices={"input_cache_hit": "0.04", "input_cache_miss": "2", "output": "8"},
        actor_id="synthetic-admin",
    )
    billing.top_up(user["id"], "3", "pipeline-credit", "synthetic-admin")
    def fake_render(bank_dir, output_dir, selected_ids, *, chromium_executable=None):
        output_dir.mkdir()
        image_root = output_dir / "images"
        image_root.mkdir()
        image_data = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image_data, format="PNG")
        image_path = image_root / "synthetic.png"
        image_path.write_bytes(image_data.getvalue())
        source = json.loads((bank_dir / "bank.json").read_bytes())
        by_id = {question["id"]: question for question in source["questions"]}
        manifest = {
            "source_bank_sha256": hashlib.sha256((bank_dir / "bank.json").read_bytes()).hexdigest(),
            "question_count": len(selected_ids),
            "questions": [
                {
                    "question_id": qid,
                    "source_hash": canonical_hash(by_id[qid]),
                    "association_status": by_id[qid]["association_status"],
                    "reliable_solution_used": by_id[qid]["association_status"] == "confirmed",
                    "text": "SYNTHETIC import question",
                    "images": [{"path": "images/synthetic.png", "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}],
                }
                for qid in selected_ids
            ],
        }
        (output_dir / "inputs.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def mock_provider(request):
        request_data = json.loads(request.content)
        prompt = request_data["messages"][1]["content"][0]["text"]
        prompt_data = json.loads(prompt.split("题目数据：", 1)[1])
        assessments = [
            {
                "question_id": item["question_id"],
                "status": "assessed",
                "point_ids": ["p1"],
                "prerequisite_ids": [],
                "difficulty_score": 50,
                "reasoning": "synthetic imported assessment",
                "point_evidence": [{"point_id": "p1", "role": "target", "reason": "synthetic source evidence"}],
                "warnings": [],
            }
            for item in prompt_data
        ]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"assessments": assessments})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    monkeypatch.setattr(admin_bank_ingest, "render_inputs", fake_render)
    real_assess = admin_bank_ingest._assess_batches

    async def mock_assess(**kwargs):
        return await real_assess(
            **kwargs, network_factory=lambda: httpx.MockTransport(mock_provider)
        )

    monkeypatch.setattr(admin_bank_ingest, "_assess_batches", mock_assess)
    task_dir = private_data / "jobs" / job["id"] / job["lease_token"]
    task_dir.mkdir(parents=True)
    result = process_import(store, settings, job, task_dir, base_bank_dir=None)
    package_root = private_data / result["package_path"]
    loaded = load_practice_bank(package_root / "source", package_root / "semantic")

    assert result["question_count"] == 1
    assert result["eligible_count"] == 1
    assert [question.status for question in loaded.questions] == ["eligible"]
    assert result["source_count"] == 2
    assert json.loads((package_root / "manifest.json").read_bytes())["schema_version"] == "question-bank-package-v1"
    wallet = billing.get_wallet(user["id"])
    assert wallet["used_credit"] != "0.000000"
    assert wallet["reserved_credit"] == "0.000000"
    store.close()
