"""Synthetic account parser/bootstrap tests; no production DB or credentials."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.model_billing import BillingStore, _Event, _Wallet
from app.web_store import Store, StoreConflict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/bootstrap_accounts.py"
_SPEC = importlib.util.spec_from_file_location("bootstrap_accounts", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
AccountSeedError = _MODULE.AccountSeedError
parse_accounts = _MODULE.parse_accounts


def _seed(users):
    return json.dumps({"version": 1, "users": users}, ensure_ascii=False).encode("utf-8")


def _user(username="Admin", password=" synthetic-password ", role="admin", **extra):
    return {
        "username": username,
        "password": password,
        "display_name": extra.pop("display_name", username),
        "role": role,
        **extra,
    }


def test_parser_normalizes_names_but_preserves_password_exactly():
    accounts = parse_accounts(
        _seed([_user("  Alice@Example.COM ", " pass with spaces ", display_name=" Alice ")])
    )
    assert accounts == [
        {
            "username": "alice@example.com",
            "password": " pass with spaces ",
            "display_name": "Alice",
            "role": "admin",
        }
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{" + b" " * (128 * 1024),
        b'{"version":true,"users":[]}',
        b'{"version":1,"users":[],"extra":1}',
        b'{"version":1,"version":1,"users":[]}',
        _seed([]),
        _seed([_user("one"), _user("ONE ")]),
        _seed([_user("one", extra={"unexpected": True})]),
        _seed([_user("one", password="")]),
        _seed([_user("one", password="x" * 1025)]),
        _seed([_user("one", display_name="   ")]),
        _seed([_user("one", role="owner")]),
    ],
)
def test_parser_rejects_malformed_or_unsafe_seed_without_echoing(raw):
    with pytest.raises(AccountSeedError) as error:
        parse_accounts(raw)
    assert str(error.value) == "账号配置无效"


def test_bootstrap_is_idempotent_and_skips_without_changing_password_or_wallet(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'bootstrap.sqlite'}")
    try:
        store.init()
        first = store.bootstrap_users([_user("Teacher", "original-password", "teacher")])
        assert first == {"created": ["teacher"], "skipped": []}
        users = store.list_users()
        user_id = users[0]["id"]
        first_wallet = BillingStore(store.engine).get_wallet(user_id)
        repeated = store.bootstrap_users([_user(" TEACHER ", "replacement-password", "teacher")])
        assert repeated == {"created": [], "skipped": ["teacher"]}
        assert store.authenticate("teacher", "original-password") is not None
        assert store.authenticate("teacher", "replacement-password") is None
        assert store.list_users()[0]["display_name"] == "Teacher"
        assert BillingStore(store.engine).get_wallet(user_id) == first_wallet
    finally:
        store.close()


def test_role_conflict_rejects_whole_batch_without_creating_other_users(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'bootstrap.sqlite'}")
    try:
        store.init()
        store.create_user("existing", "old-password", "Existing", role="teacher")
        with pytest.raises(StoreConflict):
            store.bootstrap_users(
                [
                    _user("new-admin", "new-password", "admin"),
                    _user("existing", "ignored-password", "admin"),
                ]
            )
        assert {row["username"] for row in store.list_users()} == {"existing"}
        with Session(store.engine) as session:
            assert session.get(_Wallet, "nonexistent-placeholder") is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(_Event)
                    .where(_Event.action == "initial_credit")
                )
                == 1
            )
    finally:
        store.close()


def test_wallet_gifts_and_users_roll_back_together_on_batch_failure(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'bootstrap.sqlite'}")
    store.init()
    original = BillingStore.initialize_user_wallet
    calls = 0

    def fail_on_second(self, session, user_id):
        nonlocal calls
        result = original(self, session, user_id)
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic injected failure")
        return result

    monkeypatch.setattr(BillingStore, "initialize_user_wallet", fail_on_second)
    try:
        with pytest.raises(RuntimeError, match="synthetic injected failure"):
            store.bootstrap_users(
                [
                    _user("first", "password-one", "admin"),
                    _user("second", "password-two", "teacher"),
                ]
            )
        assert store.list_users() == []
        with Session(store.engine) as session:
            assert session.scalar(select(func.count()).select_from(_Wallet)) == 0
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(_Event)
                    .where(_Event.action == "initial_credit")
                )
                == 0
            )
    finally:
        store.close()


def test_stdin_cli_outputs_names_only_and_repeated_run_is_safe(tmp_path):
    database = tmp_path / "cli.sqlite"
    environment = os.environ.copy()
    environment["TEASSISTANT_DATABASE_URL"] = f"sqlite:///{database}"
    environment["TEASSISTANT_DATA_DIR"] = str(tmp_path / "private")
    payload = _seed([_user("synthetic-admin", "SYNTHETIC_SECRET_NOT_REAL", "admin")])
    first = subprocess.run(
        [sys.executable, str(SCRIPT), "--stdin"],
        input=payload,
        capture_output=True,
        env=environment,
        timeout=30,
    )
    assert first.returncode == 0, first.stderr.decode("utf-8")
    assert b"SYNTHETIC_SECRET_NOT_REAL" not in first.stdout + first.stderr
    assert json.loads(first.stdout) == {
        "created": ["synthetic-admin"],
        "skipped": [],
        "created_count": 1,
        "skipped_count": 0,
    }
    second = subprocess.run(
        [sys.executable, str(SCRIPT), "--stdin"],
        input=_seed([_user("synthetic-admin", "DIFFERENT_SECRET_NOT_REAL", "admin")]),
        capture_output=True,
        env=environment,
        timeout=30,
    )
    assert second.returncode == 0
    assert b"DIFFERENT_SECRET_NOT_REAL" not in second.stdout + second.stderr
    assert json.loads(second.stdout)["skipped"] == ["synthetic-admin"]
    store = Store(f"sqlite:///{database}")
    try:
        assert store.authenticate("synthetic-admin", "SYNTHETIC_SECRET_NOT_REAL") is not None
        assert store.authenticate("synthetic-admin", "DIFFERENT_SECRET_NOT_REAL") is None
    finally:
        store.close()
