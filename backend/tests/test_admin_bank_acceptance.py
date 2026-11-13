"""Independent administrator-bank acceptance using synthetic and read-only fixtures."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from app import admin_bank
from app.bank_practice import (
    build_bank_practice,
    load_practice_bank,
    validate_bank_snapshot,
)
from app.contracts import PracticeRequest, Quota, StudentProgress
from app.math_latex import build_formula_catalog, validate_formula_catalog
from app.semantic_contracts import DIFFICULTY_VERSION
from app.source_exporting import _validate_snapshot_membership, _validate_source_bank
from app.web_api import create_app
from app.web_store import Store, _Job, _now
from fastapi.testclient import TestClient
from sqlalchemy import update
from test_bank_practice import _fixture
from test_web import _csrf, _login, _settings

ROOT = Path(__file__).resolve().parents[2]
REAL_PACKAGE = ROOT / "runtime" / "bank-portable"


def _setup_synthetic(tmp_path: Path):
    bank_dir, semantic_dir, curriculum, source_question = _fixture(tmp_path)
    settings = _settings(
        tmp_path,
        bank_dir=bank_dir,
        semantic_dir=semantic_dir,
        curriculum_path=semantic_dir / "curriculum.json",
    )
    store = Store(settings.database_url)
    store.init()
    admin = store.create_user("bank-admin", "synthetic-admin-password", "Synthetic Admin", "admin")
    teacher = store.create_user("bank-teacher", "synthetic-teacher-password", "Synthetic Teacher")
    app = create_app(settings=settings, store=store)
    return settings, store, app, admin, teacher, curriculum, source_question


def _set_real_package(store: Store):
    row = admin_bank.state(store)
    data = {**row["data"], "package_path": "runtime/bank-portable"}
    return store.update_record(
        "bank_state", admin_bank.STATE_ID, admin_bank.STATE_OWNER, data, row["revision"]
    )


def test_admin_bank_routes_enforce_login_role_and_csrf(tmp_path):
    settings, store, app, _admin, teacher, _curriculum, source = _setup_synthetic(tmp_path)
    path = f"/api/admin/bank/questions/{source['id']}"
    try:
        with TestClient(app, base_url="https://testserver") as client:
            assert client.get("/api/admin/bank").status_code == 401
            teacher_identity = _login(client, teacher["username"], "synthetic-teacher-password")
            assert client.get("/api/admin/bank").status_code == 403
            denied = client.patch(
                path,
                json={
                    "expected_revision": 1,
                    "enabled": True,
                    "point_ids": ["p1"],
                    "prerequisite_ids": ["p0"],
                    "difficulty_score": 55,
                    "reason": "synthetic permission check",
                },
                headers=_csrf(teacher_identity),
            )
            assert denied.status_code == 403

            # A teacher session cannot gain admin rights by attaching a CSRF token.
            assert client.get("/api/admin/bank/questions").status_code == 403

            client.post("/api/auth/logout", headers=_csrf(teacher_identity))
            admin_identity = _login(client, "bank-admin", "synthetic-admin-password")
            body = {
                "expected_revision": 1,
                "enabled": True,
                "point_ids": ["p1"],
                "prerequisite_ids": ["p0"],
                "difficulty_score": 55,
                "reason": "synthetic permission check",
            }
            assert client.patch(path, json=body).status_code == 403
            assert client.patch(path, json=body, headers=_csrf(admin_identity)).status_code == 200
    finally:
        store.close()


def test_adjustment_changes_effective_selection_without_changing_ai_or_source_evidence(tmp_path):
    settings, store, _app, _admin, _teacher, curriculum, source = _setup_synthetic(tmp_path)
    try:
        before = load_practice_bank(settings.bank_dir, settings.semantic_dir)
        original = before.questions[0]
        raw_bank = (settings.bank_dir / "bank.json").read_bytes()
        raw_annotations = (settings.semantic_dir / "annotations.json").read_bytes()
        source_doc = (settings.bank_dir / "sources" / "original.docx").read_bytes()
        call_result = (
            tmp_path / "source-run" / "calls" / "0001" / "result.json"
        ).read_bytes()
        state = admin_bank.state(store)
        point = "p0" if original.point_ids != ["p0"] else "p1"
        updated = admin_bank.save_adjustment(
            store,
            settings,
            source["id"],
            {
                "expected_revision": state["revision"],
                "enabled": True,
                "point_ids": [point],
                "prerequisite_ids": [],
                "difficulty_score": 81,
                "reason": "synthetic acceptance adjustment",
                "note": "synthetic note",
            },
            "synthetic-admin",
        )
        assert updated["revision"] == 2
        after = admin_bank.load_bank(store, settings)
        revised = after.questions[0]
        assert revised.status == "eligible"
        assert revised.point_ids == [point]
        assert revised.difficulty_score == 81
        assert revised.admin_adjustment.source == "admin_override"
        assert revised.semantic_payload == original.semantic_payload
        assert revised.source_payload == original.source_payload
        assert revised.source_hash == original.source_hash
        assert revised.semantic_hash == original.semantic_hash
        assert revised.revision != original.revision
        assert raw_bank == (settings.bank_dir / "bank.json").read_bytes()
        assert raw_annotations == (settings.semantic_dir / "annotations.json").read_bytes()
        assert source_doc == (settings.bank_dir / "sources" / "original.docx").read_bytes()
        assert call_result == (
            tmp_path / "source-run" / "calls" / "0001" / "result.json"
        ).read_bytes()

        # A new practice snapshot consumes the explicit override; old snapshots stay stale.
        progress = StudentProgress(
            student_id="synthetic-student",
            revision="progress-1",
            curriculum_version=curriculum.version,
            learned_point_ids=[p.id for p in curriculum.points],
        )
        request = PracticeRequest(
            quotas=[Quota(point_id=point, count=1)],
            difficulty_version=DIFFICULTY_VERSION,
            excluded_question_ids=[],
            seed=7,
        )
        stale_snapshot = build_bank_practice(
            before,
            curriculum,
            progress,
            PracticeRequest(
                quotas=[Quota(point_id=original.point_ids[0], count=1)],
                difficulty_version=DIFFICULTY_VERSION,
                seed=6,
            ),
            is_synthetic_student=True,
        )
        with pytest.raises(ValueError, match="hash mismatch|differs"):
            validate_bank_snapshot(stale_snapshot, after)

        current_snapshot = build_bank_practice(
            after, curriculum, progress, request, is_synthetic_student=True
        )
        assert [item.question.id for item in current_snapshot.items] == [source["id"]]
        validate_bank_snapshot(current_snapshot, after)

        # The source-native export gates accept the new metadata revision while
        # continuing to bind formulas/assets to the immutable original package.
        formula_catalog = build_formula_catalog(current_snapshot, settings.bank_dir)
        validate_formula_catalog(current_snapshot, formula_catalog, settings.bank_dir)
    finally:
        store.close()


def test_admin_adjustment_cannot_unblock_damaged_source_media(tmp_path):
    settings, store, _app, _admin, _teacher, _curriculum, source = _setup_synthetic(tmp_path)
    try:
        media = settings.bank_dir / "media" / "doc-original" / "image1.png"
        media.write_bytes(b"tampered synthetic media")
        state = admin_bank.state(store)
        adjusted = admin_bank.save_adjustment(
            store,
            settings,
            source["id"],
            {
                "expected_revision": state["revision"],
                "enabled": True,
                "point_ids": ["p1"],
                "prerequisite_ids": ["p0"],
                "difficulty_score": 80,
                "reason": "synthetic damaged-source test",
                "note": "attempt to enable cannot repair source evidence",
            },
            "synthetic-admin",
        )
        current = admin_bank.load_bank(store, settings).questions[0]
        assert current.status == "blocked"
        assert "source_media_missing_or_mismatched" in current.block_reasons
        assert "admin_disabled" not in current.block_reasons
        assert adjusted["status"] == "blocked"
    finally:
        store.close()


def test_store_multi_publication_rolls_back_earlier_cas_when_later_cas_fails(tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.database_url)
    store.init()
    try:
        teacher = store.create_user("publication-teacher", "synthetic-password", "Synthetic")
        first = store.create_record("exam", teacher["id"], {"value": "before-first"})
        second = store.create_record("bank_state", admin_bank.STATE_OWNER, {"value": "before-second"})
        queued = store.enqueue_job(teacher["id"], "analysis", "synthetic-resource", {})
        claimed = store.claim_job("synthetic-worker", lease_seconds=60)
        assert claimed["id"] == queued["id"]

        assert not store.finish_job(
            claimed["id"],
            claimed["lease_token"],
            {"published": True},
            additional_resource_updates=[
                {
                    "kind": "exam",
                    "record_id": first["id"],
                    "owner_id": teacher["id"],
                    "data": {"value": "after-first"},
                    "expected_revision": first["revision"],
                },
                {
                    "kind": "bank_state",
                    "record_id": second["id"],
                    "owner_id": admin_bank.STATE_OWNER,
                    "data": {"value": "after-second"},
                    "expected_revision": second["revision"] + 1,
                },
            ],
        )
        assert store.get_record("exam", first["id"], teacher["id"]) == first
        assert store.get_record("bank_state", second["id"], admin_bank.STATE_OWNER) == second
        assert store.get_job(queued["id"], teacher["id"])["status"] == "running"
    finally:
        store.close()


def test_old_worker_lease_cannot_publish_bank_pointer(tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.database_url)
    store.init()
    try:
        teacher = store.create_user("lease-teacher", "synthetic-password", "Synthetic")
        state = admin_bank.state(store)
        queued = store.enqueue_job(teacher["id"], "analysis", "synthetic-resource", {})
        claimed = store.claim_job("old-worker", lease_seconds=60)
        assert claimed["id"] == queued["id"]
        with store.engine.begin() as connection:
            connection.execute(
                update(_Job)
                .where(_Job.id == queued["id"])
                .values(lease_expires_at=_now() - timedelta(seconds=1))
            )
        assert not store.finish_job(
            queued["id"],
            claimed["lease_token"],
            {"imported": True},
            additional_resource_updates=[
                {
                    "kind": "bank_state",
                    "record_id": admin_bank.STATE_ID,
                    "owner_id": admin_bank.STATE_OWNER,
                    "data": {**state["data"], "package_path": "bank-candidate"},
                    "expected_revision": state["revision"],
                }
            ],
        )
        assert admin_bank.state(store) == state
        assert store.get_job(queued["id"], teacher["id"])["status"] == "running"
    finally:
        store.close()


@pytest.mark.skipif(
    not (REAL_PACKAGE / "manifest.json").is_file(),
    reason="Private real-bank package is local-only and is not included in source checkouts",
)
def test_real_runtime_package_preview_and_adjusted_snapshot_export_gates(tmp_path):
    assert REAL_PACKAGE.is_dir()
    manifest = json.loads((REAL_PACKAGE / "manifest.json").read_bytes())
    assert manifest["schema_version"] == "question-bank-package-v1"
    manifest_questions = manifest["inventory_after"]["questions"]
    assert len(manifest_questions) == 200
    assert sum(row["status"] == "eligible" for row in manifest_questions.values()) == 176
    assert sum(row["status"] == "blocked" for row in manifest_questions.values()) == 24

    # Use an in-memory database and the read-only package path. No source package
    # files, calls, reports, or deployment credentials are written or displayed.
    settings = _settings(
        tmp_path,
        data_dir=ROOT,
        bank_dir=REAL_PACKAGE / "source",
        semantic_dir=REAL_PACKAGE / "semantic",
        curriculum_path=REAL_PACKAGE / "semantic" / "curriculum.json",
    )
    store = Store(settings.database_url)
    store.init()
    try:
        _set_real_package(store)
        before_source = (REAL_PACKAGE / "source" / "bank.json").read_bytes()
        before_annotations = (REAL_PACKAGE / "semantic" / "annotations.json").read_bytes()
        before_manifest = (REAL_PACKAGE / "manifest.json").read_bytes()
        bank = admin_bank.load_bank(store, settings)
        assert sum(q.status == "eligible" for q in bank.questions) == 176
        assert sum(q.status == "blocked" for q in bank.questions) == 24
        target = next(q for q in bank.questions if q.status == "eligible")

        # Exercise the protected HTTP preview surface against the real package.
        admin = store.create_user("real-bank-admin", "synthetic-password", "Synthetic Admin", "admin")
        app = create_app(settings=settings, store=store)
        with TestClient(app, base_url="https://testserver") as client:
            identity = _login(client, admin["username"], "synthetic-password")
            summary_response = client.get("/api/admin/bank")
            assert summary_response.status_code == 200, summary_response.text
            summary = summary_response.json()
            assert (summary["question_count"], summary["eligible_count"], summary["blocked_count"]) == (200, 176, 24)
            detail_response = client.get(f"/api/admin/bank/questions/{target.id}")
            assert detail_response.status_code == 200, detail_response.text
            detail = detail_response.json()
            assert detail["question_html"]
            assert "source_payload" not in detail and "semantic_payload" not in detail
            assert "prompt.txt" not in detail_response.text
            assert "raw_json_result" not in detail_response.text
            assert str(ROOT) not in detail_response.text
            assert client.post(
                "/api/admin/bank/check",
                json={"expected_revision": summary["revision"]},
                headers=_csrf(identity),
            ).status_code == 200

        curriculum = bank.curriculum_snapshot
        all_ids = [point.id for point in curriculum.points]
        alternate = next(point_id for point_id in all_ids if point_id not in target.point_ids)
        original_point = target.point_ids[0]
        progress = StudentProgress(
            student_id="synthetic-export-student",
            revision="progress-1",
            curriculum_version=curriculum.version,
            learned_point_ids=all_ids,
        )
        original_request = PracticeRequest(
            quotas=[Quota(point_id=original_point, count=1)],
            difficulty_version=DIFFICULTY_VERSION,
            excluded_question_ids=[q.id for q in bank.questions if q.id != target.id],
            seed=11,
        )
        old_snapshot = build_bank_practice(
            bank, curriculum, progress, original_request, is_synthetic_student=True
        )
        assert [item.question.id for item in old_snapshot.items] == [target.id]

        state = admin_bank.state(store)
        adjusted_score = 37 if target.difficulty_score != 37 else 38
        admin_bank.save_adjustment(
            store,
            settings,
            target.id,
            {
                "expected_revision": state["revision"],
                "enabled": True,
                "point_ids": [alternate],
                "prerequisite_ids": [],
                "difficulty_score": adjusted_score,
                "reason": "synthetic read-only package export-gate check",
                "note": "in-memory database only",
            },
            "synthetic-admin",
        )
        after = admin_bank.load_bank(store, settings)
        changed = next(q for q in after.questions if q.id == target.id)
        assert changed.status == "eligible"
        assert changed.point_ids == [alternate]
        assert changed.difficulty_score == adjusted_score
        assert changed.semantic_payload == target.semantic_payload
        with pytest.raises(ValueError, match="hash mismatch|differs"):
            validate_bank_snapshot(old_snapshot, after)

        new_request = PracticeRequest(
            quotas=[Quota(point_id=alternate, count=1)],
            difficulty_version=DIFFICULTY_VERSION,
            excluded_question_ids=[q.id for q in after.questions if q.id != target.id],
            seed=12,
        )
        new_snapshot = build_bank_practice(
            after, curriculum, progress, new_request, is_synthetic_student=True
        )
        assert [item.question.id for item in new_snapshot.items] == [target.id]
        validate_bank_snapshot(new_snapshot, after)
        _validate_snapshot_membership(new_snapshot, after)
        _validate_source_bank(REAL_PACKAGE / "source", after, curriculum)
        catalog = build_formula_catalog(new_snapshot, REAL_PACKAGE / "source")
        validate_formula_catalog(new_snapshot, catalog, REAL_PACKAGE / "source")

        # Confirm this test only touched the separate in-memory state.
        assert before_source == (REAL_PACKAGE / "source" / "bank.json").read_bytes()
        assert before_annotations == (REAL_PACKAGE / "semantic" / "annotations.json").read_bytes()
        assert before_manifest == (REAL_PACKAGE / "manifest.json").read_bytes()
    finally:
        store.close()
