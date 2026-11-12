"""Independent new-user credit checks, using only synthetic accounts and local SQLite."""

from contextlib import contextmanager

import pytest
from app.model_billing import BillingConflict, BillingError, BillingStore
from app.web_store import Store, StoreConflict
from sqlalchemy import inspect, text

PASSWORD = "synthetic-password"


@pytest.fixture
def setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'new-user-credit.sqlite'}"
    store = Store(url)
    store.init()
    yield store, BillingStore(store.engine), url
    store.close()


def _create(store, username):
    return store.create_user(username, PASSWORD, "合成额度用户")


def _table_counts(engine):
    """Count user and audit rows without assuming the implementer's audit table names."""
    with engine.connect() as connection:
        return {
            name: connection.scalar(text(f'SELECT COUNT(*) FROM "{name}"'))
            for name in inspect(engine).get_table_names()
        }


@contextmanager
def _legacy_creation(monkeypatch):
    # A synthetic upgrade fixture: old create_user did not call this new helper.
    with monkeypatch.context() as patch:
        patch.setattr(BillingStore, "initialize_user_wallet", lambda self, session, user_id: None)
        yield


def test_defaults_apply_only_once_to_accounts_created_after_the_change(setup):
    store, billing, _ = setup
    assert billing.get_defaults() == {
        "currency": "CNY",
        "new_user_credit": "10.000000",
        "revision": 1,
    }
    first = _create(store, "default-ten")
    assert billing.get_wallet(first["id"])["total_credit"] == "10.000000"
    changed = billing.save_defaults("20.123456", 1, "synthetic-admin")
    assert changed["revision"] == 2
    second = _create(store, "custom-twenty")
    assert billing.get_wallet(second["id"])["remaining_credit"] == "20.123456"
    billing.save_defaults("0", 2, "synthetic-admin")
    third = _create(store, "default-zero")
    assert billing.get_wallet(third["id"])["total_credit"] == "0.000000"
    assert billing.get_wallet(first["id"])["total_credit"] == "10.000000"
    assert billing.get_wallet(second["id"])["total_credit"] == "20.123456"


def test_upgrade_does_not_gift_old_users_with_or_without_a_wallet(setup, monkeypatch):
    store, billing, _ = setup
    with _legacy_creation(monkeypatch):
        missing = _create(store, "legacy-no-wallet")
        existing = _create(store, "legacy-existing-wallet")
    billing.top_up(existing["id"], "7.5", "legacy-credit", "synthetic-admin")
    billing.save_defaults("20.123456", 1, "synthetic-admin")
    assert billing.get_wallet(missing["id"])["remaining_credit"] == "0.000000"
    assert billing.get_wallet(existing["id"])["total_credit"] == "7.500000"
    assert store.authenticate(missing["username"], PASSWORD)["id"] == missing["id"]
    assert store.authenticate(existing["username"], PASSWORD)["id"] == existing["id"]
    assert billing.get_wallet(missing["id"])["total_credit"] == "0.000000"
    assert billing.get_wallet(existing["id"])["total_credit"] == "7.500000"


def test_repeated_initialization_reads_and_restart_do_not_repeat_the_gift(setup):
    store, billing, url = setup
    user = _create(store, "gift-once")
    billing.save_defaults("20.123456", 1, "synthetic-admin")
    before = _table_counts(store.engine)
    for _ in range(3):
        billing.get_wallet(user["id"])
        billing.get_defaults()
        billing.init()
        with store._sessions.begin() as session:
            billing.initialize_user_wallet(session, user["id"])
    assert _table_counts(store.engine) == before
    restarted = Store(url)
    try:
        restarted.init()
        after = BillingStore(restarted.engine)
        assert after.get_defaults()["new_user_credit"] == "20.123456"
        assert after.get_wallet(user["id"])["total_credit"] == "10.000000"
        assert _table_counts(restarted.engine) == before
    finally:
        restarted.close()


def test_failure_after_wallet_initialization_rolls_back_user_wallet_and_audit(setup, monkeypatch):
    store, billing, _ = setup
    billing.get_defaults()
    before = _table_counts(store.engine)
    original = BillingStore.initialize_user_wallet

    def fail_after_initialization(self, session, user_id):
        original(self, session, user_id)
        session.flush()
        raise RuntimeError("synthetic failure after initial gift")

    monkeypatch.setattr(BillingStore, "initialize_user_wallet", fail_after_initialization)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        _create(store, "must-rollback")
    assert store.list_users() == []
    assert _table_counts(store.engine) == before


def test_duplicate_username_cannot_leave_an_orphan_gift_or_audit(setup):
    store, billing, _ = setup
    user = _create(store, "same-name")
    before = _table_counts(store.engine)
    with pytest.raises(StoreConflict):
        _create(store, "same-name")
    assert _table_counts(store.engine) == before
    assert billing.get_wallet(user["id"])["remaining_credit"] == "10.000000"


@pytest.mark.parametrize("amount", ["-1", "0.0000001", "NaN", "Infinity", True, 20.5])
def test_invalid_defaults_leave_previous_value_and_audit_unchanged(setup, amount):
    store, billing, _ = setup
    before_defaults = billing.get_defaults()
    before = _table_counts(store.engine)
    with pytest.raises(BillingError):
        billing.save_defaults(amount, 1, "synthetic-admin")
    assert billing.get_defaults() == before_defaults
    assert _table_counts(store.engine) == before


def test_stale_default_revision_cannot_change_credit_or_create_an_audit(setup):
    store, billing, _ = setup
    billing.save_defaults("20.123456", 1, "synthetic-admin")
    before = _table_counts(store.engine)
    with pytest.raises(BillingConflict):
        billing.save_defaults("0", 1, "synthetic-admin")
    assert billing.get_defaults()["new_user_credit"] == "20.123456"
    assert _table_counts(store.engine) == before


def test_admin_api_default_save_requires_role_csrf_and_cas_and_applies_to_new_account(
    setup, tmp_path
):
    from app.web_api import create_app
    from app.web_settings import WebSettings
    from fastapi.testclient import TestClient

    store, billing, url = setup
    store.create_user("synthetic-admin", PASSWORD, "合成管理员", role="admin")
    original_teacher = _create(store, "synthetic-teacher")
    settings = WebSettings(database_url=url, data_dir=tmp_path / "private")
    app = create_app(settings=settings, store=store)
    with TestClient(app) as admin, TestClient(app) as teacher:
        login = admin.post(
            "/api/auth/login", json={"username": "synthetic-admin", "password": PASSWORD}
        ).json()
        teacher_login = teacher.post(
            "/api/auth/login", json={"username": "synthetic-teacher", "password": PASSWORD}
        ).json()
        a_csrf = {"X-CSRF-Token": login["csrf_token"]}
        t_csrf = {"X-CSRF-Token": teacher_login["csrf_token"]}
        endpoint = "/api/admin/billing/defaults"
        payload = {"new_user_credit": "20.123456", "expected_revision": 1}
        assert teacher.get(endpoint).status_code == 403
        assert teacher.post(endpoint, json=payload, headers=t_csrf).status_code == 403
        assert admin.post(endpoint, json=payload).status_code == 403
        assert (
            admin.post(
                endpoint, json={**payload, "new_user_credit": True}, headers=a_csrf
            ).status_code
            == 422
        )
        changed = admin.post(endpoint, json=payload, headers=a_csrf)
        assert changed.status_code == 200
        assert changed.json()["new_user_credit"] == "20.123456"
        assert admin.post(endpoint, json=payload, headers=a_csrf).status_code == 409
        created = admin.post(
            "/api/admin/users",
            headers=a_csrf,
            json={
                "username": "synthetic-custom-credit",
                "password": PASSWORD,
                "display_name": "合成新额度账号",
                "role": "teacher",
            },
        )
        assert created.status_code == 201
        assert billing.get_wallet(created.json()["id"])["total_credit"] == "20.123456"
        assert billing.get_wallet(original_teacher["id"])["total_credit"] == "10.000000"
        assert admin.get(endpoint).json()["revision"] == 2
