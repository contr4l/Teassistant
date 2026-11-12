"""Administrator billing HTTP integration using only explicit synthetic credentials."""

import pytest
from app.model_billing import BillingStore
from app.model_settings import DEFAULT_PRICES, ModelSettingsStore
from app.web_api import create_app
from app.web_store import Store
from fastapi.testclient import TestClient
from test_web import _csrf, _login, _settings


@pytest.fixture
def setup(tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.database_url)
    store.init()
    # Keep the original zero-balance admission/idempotency scenarios explicit.
    BillingStore(store.engine).save_defaults("0", 1, "synthetic-fixture")
    admin = store.create_user("admin", "synthetic-password", "管理员", "admin")
    teacher = store.create_user("teacher", "synthetic-password", "老师")
    app = create_app(settings, store)
    yield settings, store, app, admin, teacher
    store.close()


def test_model_key_is_write_only_and_validation_never_echoes_it(setup):
    settings, _store, app, _admin, _teacher = setup
    secret = "synthetic-key-should-never-appear-in-json"
    with TestClient(app, base_url="https://testserver") as client:
        identity = _login(client, "admin", "synthetic-password")
        assert client.get("/api/admin/deepseek").json()["key_configured"] is False
        payload = {
            "expected_revision": 0,
            "api_key": secret,
            "model": "deepseek-flash",
            "prices": DEFAULT_PRICES,
        }
        saved = client.post("/api/admin/deepseek", json=payload, headers=_csrf(identity))
        assert saved.status_code == 200, saved.text
        assert saved.json()["key_configured"] and secret not in saved.text
        assert "synthetic-key" not in client.get("/api/admin/deepseek").text
        payload.pop("expected_revision")
        invalid = client.post("/api/admin/deepseek", json=payload, headers=_csrf(identity))
        assert invalid.status_code == 422 and secret not in invalid.text
        assert ModelSettingsStore(settings.data_dir).runtime()[0].api_key == secret


def test_teacher_cannot_manage_keys_credit_or_other_wallets(setup):
    _settings_value, _store, app, admin, teacher = setup
    with TestClient(app, base_url="https://testserver") as client:
        teacher_session = _login(client, "teacher", "synthetic-password")
        for path in [
            "/api/admin/deepseek",
            "/api/admin/billing/wallets",
            "/api/admin/billing/yesterday",
            "/api/admin/billing/unresolved",
        ]:
            assert client.get(path).status_code == 403
        denied = client.post(
            f"/api/admin/billing/users/{admin['id']}/credits",
            json={"amount": "10", "request_id": "forged"},
            headers=_csrf(teacher_session),
        )
        assert denied.status_code == 403
        mine = client.get("/api/billing/me")
        assert mine.status_code == 200 and mine.json()["user_id"] == teacher["id"]
        assert client.get(f"/api/billing/{admin['id']}").status_code == 404


def test_topup_is_additive_idempotent_and_requires_csrf(setup):
    _settings_value, _store, app, _admin, teacher = setup
    path = f"/api/admin/billing/users/{teacher['id']}/credits"
    with TestClient(app, base_url="https://testserver") as client:
        identity = _login(client, "admin", "synthetic-password")
        body = {"amount": "10.000001", "request_id": "topup-synthetic-1"}
        assert client.post(path, json=body).status_code == 403
        first = client.post(path, json=body, headers=_csrf(identity))
        assert first.status_code == 200, first.text
        repeat = client.post(path, json=body, headers=_csrf(identity))
        assert repeat.json()["total_credit"] == "10.000001"
        second = client.post(
            path, json={"amount": "2", "request_id": "topup-synthetic-2"}, headers=_csrf(identity)
        )
        assert second.json()["total_credit"] == "12.000001"
        conflict = client.post(
            path, json={"amount": "99", "request_id": body["request_id"]}, headers=_csrf(identity)
        )
        assert conflict.status_code == 409
        assert (
            client.post(
                path, json={"amount": "NaN", "request_id": "bad"}, headers=_csrf(identity)
            ).status_code
            == 422
        )


def test_unknown_hold_can_be_resolved_through_admin_api(setup):
    _settings_value, store, app, admin, teacher = setup
    billing = BillingStore(store.engine)
    billing.top_up(teacher["id"], "10", "seed-credit", admin["id"])
    call = billing.reserve_attempt(
        teacher["id"],
        "synthetic-job",
        "paper",
        "synthetic-operation",
        1,
        "deepseek-flash",
        0,
        DEFAULT_PRICES,
        8192,
    )
    billing.settle_attempt(call["id"], None, None, "timeout")
    with TestClient(app, base_url="https://testserver") as client:
        identity = _login(client, "admin", "synthetic-password")
        rows = client.get("/api/admin/billing/unresolved").json()["calls"]
        assert len(rows) == 1 and rows[0]["id"] == call["id"]
        result = client.post(
            f"/api/admin/billing/calls/{call['id']}/resolve",
            json={"amount": "0", "reason": "合成测试：已核实未消费", "request_id": "resolve-1"},
            headers=_csrf(identity),
        )
        assert result.status_code == 200, result.text
        assert client.get("/api/admin/billing/unresolved").json()["calls"] == []
        assert billing.get_wallet(teacher["id"])["remaining_credit"] == "10.000000"


def test_new_install_yesterday_is_not_presented_as_tracked_zero(setup):
    _settings_value, _store, app, _admin, _teacher = setup
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, "admin", "synthetic-password")
        response = client.get("/api/admin/billing/yesterday")
        assert response.status_code == 200
        assert response.json()["coverage"] == "not_tracked"
        assert response.json()["tracking_started_at"]


def test_active_reservation_cannot_be_refunded_but_interrupted_job_can_be_resolved(setup):
    _settings_value, store, app, admin, teacher = setup
    billing = BillingStore(store.engine)
    billing.top_up(teacher["id"], "10", "active-credit", admin["id"])
    exam = store.create_record("exam", teacher["id"], {})
    store.enqueue_job(teacher["id"], "analysis", exam["id"], {})
    job = store.claim_job("synthetic-worker")
    call = billing.reserve_attempt(
        teacher["id"], job["id"], "paper", "active-op", 1, "deepseek-flash", 0, DEFAULT_PRICES, 8192
    )
    with TestClient(app, base_url="https://testserver") as client:
        identity = _login(client, "admin", "synthetic-password")
        rows = client.get("/api/admin/billing/unresolved").json()["calls"]
        assert rows[0]["can_resolve"] is False
        payload = {"amount": "0", "reason": "synthetic review", "request_id": "resolve-active"}
        path = f"/api/admin/billing/calls/{call['id']}/resolve"
        assert client.post(path, json=payload, headers=_csrf(identity)).status_code == 409
        assert billing.get_wallet(teacher["id"])["reserved_credit"] == "2.162688"
        store.finish_job(
            job["id"], job["lease_token"], None, status="failed", error="synthetic interruption"
        )
        assert client.get("/api/admin/billing/unresolved").json()["calls"][0]["can_resolve"] is True
        resolved = client.post(path, json=payload, headers=_csrf(identity))
        assert resolved.status_code == 200, resolved.text
        assert billing.get_wallet(teacher["id"])["remaining_credit"] == "10.000000"
