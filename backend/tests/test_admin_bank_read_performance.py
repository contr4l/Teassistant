"""Synthetic HTTP and state transitions protect cached shared-bank behavior."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from app import admin_bank, bank_practice, web_jobs
from app.bank_cache import clear_bank_cache
from fastapi.testclient import TestClient
from test_admin_bank import _patch
from test_admin_bank_acceptance import _csrf, _login, _setup_synthetic
from test_web_jobs import setup


@pytest.fixture(autouse=True)
def isolated_cache():
    clear_bank_cache()
    admin_bank._source_titles.cache_clear()
    yield
    clear_bank_cache()
    admin_bank._source_titles.cache_clear()


def _loader_calls(monkeypatch):
    calls = []
    original = bank_practice.load_practice_bank

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(bank_practice, "load_practice_bank", counted)
    return calls


def test_summary_list_detail_and_teacher_inventory_reuse_verified_base(tmp_path, monkeypatch):
    store, settings, teacher, student, _practice = setup(tmp_path)
    calls = _loader_calls(monkeypatch)
    summary = admin_bank.summary(store, settings)
    page = admin_bank.list_questions(store, settings, limit=25)
    detail = admin_bank.detail(store, settings, page["items"][0]["id"])
    inventory = web_jobs.get_inventory(store, settings, teacher["id"], student["id"])
    assert len(calls) == 1
    assert summary["question_count"] == page["total"] == 1
    assert detail["status"] == "eligible"
    assert inventory["eligible_within_progress"] == 1


def test_http_check_forces_full_validation_despite_a_warm_cache(tmp_path, monkeypatch):
    _settings, _store, app, admin, _teacher, _curriculum, _source = _setup_synthetic(tmp_path)
    calls = _loader_calls(monkeypatch)
    with TestClient(app, base_url="https://testserver") as client:
        identity = _login(client, admin["username"], "synthetic-admin-password")
        assert client.get("/api/admin/bank").status_code == 200
        assert client.get("/api/admin/bank/questions").status_code == 200
        assert len(calls) == 1
        response = client.post("/api/admin/bank/check", json={"expected_revision": 1},
                               headers=_csrf(identity))
        assert response.status_code == 200
        assert len(calls) == 2
        assert client.get("/api/admin/bank").status_code == 200
        assert len(calls) == 2


def test_admin_changes_are_current_without_reloading_unchanged_source(tmp_path, monkeypatch):
    store, settings, teacher, student, _practice = setup(tmp_path)
    calls = _loader_calls(monkeypatch)
    first = admin_bank.load_bank(store, settings)
    qid = first.questions[0].id
    changed = admin_bank.save_adjustment(store, settings, qid, _patch(enabled=False), teacher["id"])
    assert changed["disabled"]
    assert admin_bank.summary(store, settings)["disabled_count"] == 1
    assert web_jobs.get_inventory(store, settings, teacher["id"], student["id"])[
        "eligible_within_progress"] == 0
    assert len(calls) == 1
    assert first.questions[0].status == "eligible"
    admin_bank.restore(store, settings, "r1", 2, teacher["id"], "synthetic restore")
    assert len(calls) == 2
    assert admin_bank.load_bank(store, settings) == first
    assert len(calls) == 2


def test_current_application_curriculum_is_checked_on_cache_hits(tmp_path, monkeypatch):
    store, settings, _teacher, _student, _practice = setup(tmp_path)
    calls = _loader_calls(monkeypatch)
    assert admin_bank.summary(store, settings)["eligible_count"] == 1
    curriculum = json.loads(settings.curriculum_path.read_bytes())
    curriculum["points"][0]["name"] = "different synthetic curriculum"
    alternate = tmp_path / "different-curriculum.json"
    alternate.write_text(json.dumps(curriculum))
    with pytest.raises(admin_bank.BankError, match="考点目录不一致"):
        admin_bank.summary(store, replace(settings, curriculum_path=alternate))
    assert len(calls) == 1


def test_only_recent_thirty_imports_issue_job_queries(tmp_path, monkeypatch):
    store, settings, teacher, _student, _practice = setup(tmp_path)
    for i in range(34):
        store.create_record("bank_import", teacher["id"], {"reason": f"synthetic {i}", "files": []})
    calls = []
    original = store.list_jobs

    def jobs(*args, **kwargs):
        calls.append(kwargs["resource_id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "list_jobs", jobs)
    result = admin_bank.summary(store, settings, requester_id=teacher["id"])
    expected = list(reversed(store.list_records("bank_import", teacher["id"])[-30:]))
    assert [item["id"] for item in result["imports"]] == [item["id"] for item in expected]
    assert len(calls) == 30


def test_titles_reuse_file_metadata_and_do_not_leak_mutations(tmp_path, monkeypatch):
    _store, settings, _teacher, _student, _practice = setup(tmp_path)
    path = settings.bank_dir / "bank.json"
    source = json.loads(path.read_bytes())
    source["pairs"] = [{"id": "synthetic-pair", "title": "synthetic title"}]
    path.write_text(json.dumps(source))
    reads = []
    original = Path.read_bytes

    def read_bytes(current):
        if current == path:
            reads.append(current)
        return original(current)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    first = admin_bank.source_titles(settings)
    assert len(reads) == 1
    first["synthetic-corruption"] = "not a title"
    assert "synthetic-corruption" not in admin_bank.source_titles(settings)
    assert len(reads) == 1
    data = json.loads(original(path))
    data["pairs"][0]["title"] = "updated synthetic title"
    path.write_text(json.dumps(data))
    assert "updated synthetic title" in admin_bank.source_titles(settings).values()
    assert len(reads) == 2
