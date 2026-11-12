"""Independent synthetic account bootstrap checks; Docker and boards are never contacted."""

import importlib.util
import json
import multiprocessing
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.model_billing import BillingStore
from app.web_store import Store, StoreConflict
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[2]
SECRET = "SYNTHETIC_BOOTSTRAP_SECRET_$(not-a-command);"
SEEDS = [
    {
        "username": "synthetic-admin",
        "password": SECRET,
        "display_name": "合成管理员",
        "role": "admin",
    },
    {
        "username": "synthetic-teacher",
        "password": "SYNTHETIC_SECOND_SECRET",
        "display_name": "合成老师",
        "role": "teacher",
    },
]


def _script(name):
    path = ROOT / "scripts" / f"{name}.py"
    assert path.is_file(), "implementation must exist before negative checks"
    spec = importlib.util.spec_from_file_location(f"independent_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _raw(users=None):
    return json.dumps(
        {"version": 1, "users": users if users is not None else SEEDS}, ensure_ascii=False
    ).encode()


def _counts(engine):
    with engine.connect() as connection:
        return {
            name: connection.scalar(text(f'SELECT COUNT(*) FROM "{name}"'))
            for name in inspect(engine).get_table_names()
        }


@pytest.fixture
def store(tmp_path):
    value = Store(f"sqlite:///{tmp_path / 'synthetic-bootstrap.sqlite'}")
    value.init()
    yield value
    value.close()


def test_parser_normalizes_names_without_trimming_password_and_needs_only_stdlib():
    accounts = deepcopy(SEEDS)
    accounts[0].update(
        username="  SYNTHETIC-ADMIN  ", password="  " + SECRET + "  ", display_name=" 合成管理员 "
    )
    parsed = _script("bootstrap_accounts").parse_accounts(_raw(accounts))
    assert parsed[0]["username"] == "synthetic-admin"
    assert parsed[0]["display_name"] == "合成管理员"
    assert parsed[0]["password"] == "  " + SECRET + "  "
    program = """import importlib.util,sys
spec=importlib.util.spec_from_file_location('isolated_seed_parser',sys.argv[1])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
spec.loader.exec_module(module)
print(len(module.parse_accounts(sys.stdin.buffer.read())))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", program, str(ROOT / "scripts/bootstrap_accounts.py")],
        input=_raw(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"2"
    assert SECRET.encode() not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "damage",
    [
        "version-bool",
        "extra-field",
        "duplicate-normalized",
        "password-bool",
        "blank-name",
        "bad-role",
        "too-many",
        "too-large",
        "bad-json",
    ],
)
def test_parser_fails_safely_without_echoing_secret_inputs(damage):
    module = _script("bootstrap_accounts")
    body = {"version": 1, "users": deepcopy(SEEDS)}
    if damage == "version-bool":
        body["version"] = True
    elif damage == "extra-field":
        body["users"][0]["private-extra"] = SECRET
    elif damage == "duplicate-normalized":
        body["users"][1]["username"] = " SYNTHETIC-ADMIN "
    elif damage == "password-bool":
        body["users"][1]["password"] = True
    elif damage == "blank-name":
        body["users"][1]["display_name"] = "  "
    elif damage == "bad-role":
        body["users"][1]["role"] = "root"
    elif damage == "too-many":
        body["users"] = [dict(SEEDS[0], username=f"synthetic-{i}") for i in range(101)]
    raw = json.dumps(body).encode()
    if damage == "too-large":
        raw = b" " * (128 * 1024 + 1)
    elif damage == "bad-json":
        raw = SECRET.encode()
    with pytest.raises(module.AccountSeedError) as error:
        module.parse_accounts(raw)
    assert SECRET not in str(error.value)


def test_bootstrap_same_role_skips_without_resetting_password_display_or_credit(store):
    user = store.create_user("synthetic-teacher", "SYNTHETIC_OLD_PASSWORD", "原合成显示名")
    billing = BillingStore(store.engine)
    billing.top_up(user["id"], "7.5", "existing-credit", "synthetic-admin")
    before = billing.get_wallet(user["id"])
    first = store.bootstrap_users(deepcopy(SEEDS))
    assert first == {"created": ["synthetic-admin"], "skipped": ["synthetic-teacher"]}
    second = store.bootstrap_users(deepcopy(SEEDS))
    assert set(second["skipped"]) == {"synthetic-admin", "synthetic-teacher"}
    assert second["created"] == []
    assert billing.get_wallet(user["id"]) == before
    authenticated = store.authenticate("synthetic-teacher", "SYNTHETIC_OLD_PASSWORD")
    assert authenticated["display_name"] == "原合成显示名"
    assert authenticated["role"] == "teacher"
    assert store.authenticate("synthetic-teacher", SEEDS[1]["password"]) is None
    assert SECRET not in json.dumps(first)


def test_role_conflict_rejects_the_whole_batch_without_creating_the_first_user(store):
    store.create_user("synthetic-teacher", "SYNTHETIC_OLD_PASSWORD", "合成老师")
    before = _counts(store.engine)
    accounts = deepcopy(SEEDS)
    accounts[1]["role"] = "admin"
    with pytest.raises(StoreConflict):
        store.bootstrap_users(accounts)
    assert _counts(store.engine) == before
    assert [user["username"] for user in store.list_users()] == ["synthetic-teacher"]


def test_second_wallet_failure_rolls_back_all_users_gifts_and_audits(store, monkeypatch):
    before = _counts(store.engine)
    original = BillingStore.initialize_user_wallet
    initialized = []

    def fail_on_second(self, session, user_id):
        result = original(self, session, user_id)
        session.flush()
        initialized.append(user_id)
        if len(initialized) == 2:
            raise RuntimeError("synthetic second-wallet failure")
        return result

    monkeypatch.setattr(BillingStore, "initialize_user_wallet", fail_on_second)
    with pytest.raises(RuntimeError, match="synthetic second-wallet"):
        store.bootstrap_users(deepcopy(SEEDS))
    assert len(initialized) == 2
    assert _counts(store.engine) == before
    assert store.list_users() == []


def test_existing_legacy_user_without_wallet_is_not_given_bootstrap_credit(store, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(BillingStore, "initialize_user_wallet", lambda self, session, user_id: None)
        legacy = store.create_user("synthetic-teacher", "SYNTHETIC_OLD_PASSWORD", "合成旧账号")
    before = _counts(store.engine)
    result = store.bootstrap_users([deepcopy(SEEDS[1])])
    assert result == {"created": [], "skipped": ["synthetic-teacher"]}
    assert _counts(store.engine) == before
    assert BillingStore(store.engine).get_wallet(legacy["id"])["total_credit"] == "0.000000"


def _bootstrap_process(url, barrier, queue, index):
    value = Store(url)
    shared_role = "admin" if index == 0 else "teacher"
    users = [
        dict(SEEDS[0], username=f"unique-{index}"),
        dict(SEEDS[1], username="shared-account", role=shared_role),
    ]
    try:
        barrier.wait(timeout=15)
        try:
            value.bootstrap_users(users)
            queue.put("created")
        except StoreConflict:
            queue.put("conflict")
        except Exception as error:
            queue.put(type(error).__name__)
    finally:
        value.close()


def test_two_processes_with_role_conflict_never_leave_half_a_losing_batch(tmp_path):
    url = f"sqlite:///{tmp_path / 'concurrent-bootstrap.sqlite'}"
    value = Store(url)
    value.init()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    queue = context.Queue()
    children = [
        context.Process(target=_bootstrap_process, args=(url, barrier, queue, i)) for i in range(2)
    ]
    try:
        for child in children:
            child.start()
        barrier.wait(timeout=15)
        results = [queue.get(timeout=15) for _ in children]
        for child in children:
            child.join(timeout=15)
            assert child.exitcode == 0
        assert sorted(results) == ["conflict", "created"]
        users = value.list_users()
        assert len(users) == 2
        assert sum(u["username"] == "shared-account" for u in users) == 1
        assert all(
            BillingStore(value.engine).get_wallet(u["id"])["total_credit"] == "10.000000"
            for u in users
        )
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        queue.close()
        value.close()


@pytest.fixture
def orchestration(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("TEASSISTANT_", "DEEPSEEK_", "COMPOSE_")):
            monkeypatch.delenv(key)
    module = _script("deploy")
    accounts = tmp_path / "accounts.json"
    accounts.write_bytes(_raw())
    accounts.chmod(0o600)
    env = tmp_path / "deployment.env"
    calls = []

    def run(args, **kwargs):
        calls.append(
            {
                "args": list(args),
                "input": kwargs.get("input"),
                "env_contains_secret": SECRET in repr(kwargs.get("env", {})),
                "shell": kwargs.get("shell", False),
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout="" if kwargs.get("text") else b"",
            stderr="" if kwargs.get("text") else b"",
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    return module, accounts, env, calls


def test_bootstrap_orchestration_sends_secret_only_to_stdin_and_preserves_existing_env(
    orchestration, capsys
):
    module, accounts, env, calls = orchestration
    args = ["--env-file", str(env), "bootstrap", "--build", "--accounts-file", str(accounts)]
    assert module.main(args) == 0
    seed = [call for call in calls if "/app/scripts/bootstrap_accounts.py" in call["args"]]
    assert len(seed) == 1
    assert seed[0]["input"] == accounts.read_bytes()
    assert seed[0]["args"][-5:] == [
        "-T",
        "api",
        "/app/.venv/bin/python",
        "/app/scripts/bootstrap_accounts.py",
        "--stdin",
    ]
    for call in calls:
        assert not call["shell"] and not call["env_contains_secret"]
        assert SECRET not in " ".join(call["args"])
        if call is not seed[0]:
            assert call["input"] is None
    before = env.read_bytes()
    calls.clear()
    assert module.main(args) == 0
    assert env.read_bytes() == before
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("damage", ["public-mode", "symlink", "no-admin", "invalid-json"])
def test_account_precheck_fails_before_env_creation_or_any_docker_action(
    orchestration, damage, capsys
):
    module, accounts, env, calls = orchestration
    if damage == "public-mode":
        accounts.chmod(0o644)
    elif damage == "symlink":
        target = accounts.with_name("actual-private.json")
        accounts.rename(target)
        accounts.symlink_to(target)
    elif damage == "no-admin":
        accounts.write_bytes(_raw([SEEDS[1]]))
    else:
        accounts.write_bytes(SECRET.encode())
    assert module.main(["--env-file", str(env), "bootstrap", "--accounts-file", str(accounts)]) == 2
    assert not env.exists() and not calls
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_startup_failure_never_runs_account_seed_or_echoes_subprocess_output(
    orchestration, monkeypatch, capsys
):
    module, accounts, env, calls = orchestration
    original = module.subprocess.run

    def fail_up(args, **kwargs):
        if "up" in args:
            raise subprocess.CalledProcessError(1, args, output=SECRET, stderr=SECRET)
        return original(args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", fail_up)
    assert module.main(["--env-file", str(env), "bootstrap", "--accounts-file", str(accounts)]) == 2
    assert not any("/app/scripts/bootstrap_accounts.py" in call["args"] for call in calls)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
