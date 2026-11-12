"""Independent synthetic billing checks; no provider or credential is contacted."""

import asyncio
import json
import multiprocessing
import stat
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from app import model_billing as billing_module
from app.model_billing import BillingConflict, BillingError, BillingStore, InsufficientCredit
from sqlalchemy import create_engine

PRICES = {"input_cache_hit": "0.04", "input_cache_miss": "2", "output": "8"}
RESERVATION = "2.162688"
USAGE = {
    "prompt_tokens": 1000,
    "prompt_cache_hit_tokens": 200,
    "prompt_cache_miss_tokens": 800,
    "completion_tokens": 100,
    "total_tokens": 1100,
}


def _reserve(store, user="synthetic-teacher", **changes):
    args = {
        "user_id": user,
        "job_id": "synthetic-job",
        "stage": "paper",
        "operation_id": "synthetic-operation",
        "attempt_no": 1,
        "model": "deepseek-flash",
        "config_revision": 1,
        "prices": dict(PRICES),
        "max_output_tokens": 8192,
    }
    args.update(changes)
    return store.reserve_attempt(**args)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    clock = {"now": datetime(2026, 2, 26, tzinfo=UTC)}
    monkeypatch.setattr(billing_module, "_now", lambda: clock["now"])
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic-billing.sqlite'}")
    store = BillingStore(engine)
    store.init()
    yield store, clock
    engine.dispose()


def _competing_process(database_url, barrier, results, operation):
    """A real second interpreter shares only the database and synchronization."""
    engine = create_engine(database_url, connect_args={"timeout": 15})
    store = BillingStore(engine)
    try:
        barrier.wait(timeout=15)
        try:
            row = _reserve(store, operation_id=operation)
            results.put(("reserved", row["reserved_credit"]))
        except InsufficientCredit:
            results.put(("insufficient", None))
        except Exception as error:
            results.put(("unexpected", type(error).__name__))
    finally:
        engine.dispose()


def test_two_processes_cannot_both_spend_the_last_reservation(tmp_path):
    url = f"sqlite:///{tmp_path / 'process-wallet.sqlite'}"
    engine = create_engine(url, connect_args={"timeout": 15})
    store = BillingStore(engine)
    store.init()
    store.top_up("synthetic-teacher", RESERVATION, "synthetic-credit", "synthetic-admin")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    results = context.Queue()
    children = [
        context.Process(target=_competing_process, args=(url, barrier, results, operation))
        for operation in ("synthetic-parent-report", "synthetic-child-cli")
    ]
    try:
        for child in children:
            child.start()
        barrier.wait(timeout=15)
        outcomes = [results.get(timeout=15) for _ in children]
        for child in children:
            child.join(timeout=15)
            assert not child.is_alive()
            assert child.exitcode == 0
        assert sorted(status for status, _ in outcomes) == ["insufficient", "reserved"]
        wallet = store.get_wallet("synthetic-teacher")
        assert wallet["used_credit"] == "0.000000"
        assert wallet["reserved_credit"] == RESERVATION
        assert wallet["remaining_credit"] == "0.000000"
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        results.close()
        engine.dispose()


def test_lifetime_credit_is_additive_and_idempotent_not_a_monthly_reset(ledger):
    store, clock = ledger
    assert store.get_wallet("synthetic-teacher")["remaining_credit"] == "0.000000"
    store.top_up("synthetic-teacher", "3.5", "credit-one", "synthetic-admin")
    store.top_up("synthetic-teacher", "3.5", "credit-one", "synthetic-admin")
    with pytest.raises(BillingConflict):
        store.top_up("synthetic-teacher", "4", "credit-one", "synthetic-admin")
    store.top_up("synthetic-teacher", "0.5", "credit-two", "synthetic-admin")
    clock["now"] += timedelta(days=400)
    assert store.get_wallet("synthetic-teacher")["total_credit"] == "4.000000"


@pytest.mark.parametrize("amount", [True, 1.5, "NaN", "Infinity", "-1", "0.0000001"])
def test_credit_rejects_non_decimal_or_sub_micro_values_without_changing_wallet(ledger, amount):
    store, _ = ledger
    with pytest.raises(BillingError):
        store.top_up("synthetic-teacher", amount, "invalid-credit", "synthetic-admin")
    assert store.get_wallet("synthetic-teacher")["total_credit"] == "0.000000"


def test_duplicate_attempt_cannot_reserve_twice(ledger):
    store, _ = ledger
    store.top_up("synthetic-teacher", RESERVATION, "credit-one", "synthetic-admin")
    first = _reserve(store)
    second = _reserve(store)
    assert first["id"] == second["id"]
    assert first["created"] is True
    assert second["created"] is False
    assert store.get_wallet("synthetic-teacher")["reserved_credit"] == RESERVATION


def test_timeout_and_repeated_unknown_settlement_do_not_refund_a_retry(ledger):
    store, _ = ledger
    store.top_up("synthetic-teacher", RESERVATION, "credit-one", "synthetic-admin")
    row = _reserve(store)
    for _ in range(2):
        store.settle_attempt(row["id"], None, None, "timeout")
    with pytest.raises(InsufficientCredit):
        _reserve(store, attempt_no=2)
    wallet = store.get_wallet("synthetic-teacher")
    assert wallet["reserved_credit"] == RESERVATION
    assert wallet["used_credit"] == "0.000000"
    assert len(store.list_unresolved()) == 1


def test_known_usage_is_charged_despite_schema_failure_using_frozen_prices(ledger):
    store, _ = ledger
    store.top_up("synthetic-teacher", "3", "credit-one", "synthetic-admin")
    prices = dict(PRICES)
    row = _reserve(store, prices=prices)
    prices["output"] = "800"
    store.settle_attempt(row["id"], dict(USAGE), 200, "schema_invalid")
    store.settle_attempt(row["id"], dict(USAGE), 200, "schema_invalid")
    wallet = store.get_wallet("synthetic-teacher")
    assert wallet["used_credit"] == "0.002408"
    assert wallet["reserved_credit"] == "0.000000"
    assert wallet["remaining_credit"] == "2.997592"


def test_sub_micro_positive_cost_and_reservation_round_up_instead_of_becoming_free(ledger):
    store, _ = ledger
    store.top_up("synthetic-teacher", "0.000002", "micro-credit", "synthetic-admin")
    tiny_prices = {name: "0.000001" for name in PRICES}
    row = _reserve(store, prices=tiny_prices)
    assert row["reserved_credit"] == "0.000002"
    usage = {
        "prompt_tokens": 1,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 1,
        "completion_tokens": 0,
        "total_tokens": 1,
    }
    store.settle_attempt(row["id"], usage, 200)
    assert store.get_wallet("synthetic-teacher")["used_credit"] == "0.000001"


def test_manual_unknown_resolution_is_bounded_and_idempotent(ledger):
    store, _ = ledger
    store.top_up("synthetic-teacher", RESERVATION, "credit-one", "synthetic-admin")
    row = _reserve(store)
    store.settle_attempt(row["id"], None, 200, "usage_missing")
    with pytest.raises(BillingError):
        store.resolve_attempt(row["id"], "2.162689", "synthetic verification", "r-bad", "admin")
    store.resolve_attempt(row["id"], "0", "synthetic verified no charge", "r-good", "admin")
    store.resolve_attempt(row["id"], "0", "synthetic verified no charge", "r-good", "admin")
    with pytest.raises(BillingConflict):
        store.resolve_attempt(row["id"], "1", "different verification", "r-good", "admin")
    wallet = store.get_wallet("synthetic-teacher")
    assert wallet["remaining_credit"] == RESERVATION
    assert wallet["used_credit"] == "0.000000"
    assert wallet["reserved_credit"] == "0.000000"


@pytest.mark.parametrize(
    "bad_usage",
    [
        None,
        {**USAGE, "completion_tokens": True},
        {**USAGE, "prompt_tokens": -1},
        {**USAGE, "prompt_cache_miss_tokens": 999999},
    ],
)
def test_invalid_or_missing_usage_stays_unknown_without_free_refund(ledger, bad_usage):
    store, _ = ledger
    store.top_up("synthetic-teacher", RESERVATION, "credit-one", "synthetic-admin")
    row = _reserve(store)
    store.settle_attempt(row["id"], bad_usage, 200)
    wallet = store.get_wallet("synthetic-teacher")
    assert wallet["reserved_credit"] == RESERVATION
    assert wallet["used_credit"] == "0.000000"
    assert len(store.list_unresolved()) == 1


def test_usage_exceeding_reservation_freezes_instead_of_clamping_cost(ledger):
    store, _ = ledger
    store.top_up("synthetic-teacher", "100", "credit-one", "synthetic-admin")
    row = _reserve(store)
    usage = {
        "prompt_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "completion_tokens": 1_000_000,
        "total_tokens": 1_000_000,
    }
    store.settle_attempt(row["id"], usage, 200)
    wallet = store.get_wallet("synthetic-teacher")
    assert wallet["frozen"] is True
    assert wallet["reserved_credit"] == RESERVATION
    assert wallet["used_credit"] == "0.000000"
    with pytest.raises(InsufficientCredit):
        _reserve(store, attempt_no=2)


def test_yesterday_uses_shanghai_half_open_day_and_retains_unknown_cost(ledger):
    store, clock = ledger
    for user in ("a-high", "z-unknown", "excluded"):
        store.top_up(user, "100", f"credit-{user}", "synthetic-admin")
    calls = [
        (datetime(2026, 2, 27, 15, 59, 59, tzinfo=UTC), "excluded", "before", USAGE),
        (datetime(2026, 2, 27, 16, tzinfo=UTC), "z-unknown", "start", USAGE),
        (
            datetime(2026, 2, 28, 8, tzinfo=UTC),
            "a-high",
            "middle",
            {
                "prompt_tokens": 2000,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 2000,
                "completion_tokens": 0,
                "total_tokens": 2000,
            },
        ),
        (datetime(2026, 2, 28, 15, 59, 59, tzinfo=UTC), "z-unknown", "unknown", None),
        (datetime(2026, 2, 28, 16, tzinfo=UTC), "excluded", "end", USAGE),
    ]
    for started, user, operation, usage in calls:
        clock["now"] = started
        row = _reserve(store, user, operation_id=operation)
        store.settle_attempt(row["id"], usage, 200)
    summary = store.daily_summary(now=datetime(2026, 3, 1, 1, tzinfo=UTC))
    assert str(summary["day"]) == "2026-02-28"
    assert summary["timezone"] == "Asia/Shanghai"
    assert summary["coverage"] == "tracked"
    assert summary["call_count"] == 3
    assert summary["unknown_count"] == 1
    assert summary["known_cost"] == "0.006408"
    assert [row["user_id"] for row in summary["users"]] == ["a-high", "z-unknown"]
    unknown = summary["users"][1]
    assert unknown["estimated_cost"] is None
    assert unknown["reserved_cost"] == RESERVATION
    assert store.daily_summary(day=date(2026, 2, 25))["coverage"] == "not_tracked"


def test_settings_key_is_private_write_only_and_admin_overrides_environment(tmp_path, monkeypatch):
    from app.model_settings import ModelSettingsStore

    environment_key = "SYNTHETIC_ENVIRONMENT_KEY_NOT_REAL"
    administrator_key = "SYNTHETIC_ADMINISTRATOR_KEY_NOT_REAL"
    monkeypatch.setenv("DEEPSEEK_API_KEY", environment_key)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    settings = ModelSettingsStore(tmp_path)
    assert settings.public()["key_source"] == "environment"
    public = settings.save(
        expected_revision=0,
        api_key=administrator_key,
        model="deepseek-flash",
        prices=PRICES,
        actor_id="synthetic-admin",
    )
    assert public["key_source"] == "admin"
    assert stat.S_IMODE(settings.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.directory.stat().st_mode) == 0o700
    assert environment_key not in json.dumps(public)
    assert administrator_key not in json.dumps(public)
    config, frozen = settings.runtime()
    assert config.api_key == administrator_key
    assert administrator_key not in repr(config)
    assert administrator_key not in json.dumps(frozen)
    settings.save(
        expected_revision=public["revision"],
        api_key="",
        model="deepseek-flash",
        prices=PRICES,
        actor_id="synthetic-admin",
    )
    assert settings.runtime()[0].api_key == administrator_key


def test_settings_stale_revision_cannot_replace_key_or_prices(tmp_path, monkeypatch):
    from app.model_settings import ModelSettingsConflict, ModelSettingsStore

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = ModelSettingsStore(tmp_path)
    settings.save(
        expected_revision=0,
        api_key="SYNTHETIC_ORIGINAL_KEY",
        model="deepseek-flash",
        prices=PRICES,
        actor_id="synthetic-admin",
    )
    before = settings.path.read_bytes()
    with pytest.raises(ModelSettingsConflict):
        settings.save(
            expected_revision=0,
            api_key="SYNTHETIC_REPLACEMENT_KEY",
            model="deepseek-flash",
            prices={**PRICES, "output": "800"},
            actor_id="synthetic-admin",
        )
    assert settings.path.read_bytes() == before


@pytest.fixture
def metered_environment(tmp_path):
    from app.model_runtime import BillingContext
    from app.model_settings import ModelSettingsStore
    from app.web_settings import WebSettings
    from app.web_store import Store

    settings = WebSettings(
        database_url=f"sqlite:///{tmp_path / 'metered.sqlite'}", data_dir=tmp_path / "private"
    )
    store = Store(settings.database_url)
    store.init()
    BillingStore(store.engine).save_defaults("0", 1, "synthetic-fixture-admin")
    user = store.create_user("billing-acceptance", "synthetic-password", "合成用户")
    job = store.enqueue_job(user["id"], "analysis", "synthetic-exam", {})
    claim = store.claim_job("synthetic-worker")
    assert claim["id"] == job["id"]
    ModelSettingsStore(settings.data_dir).save(
        expected_revision=0,
        api_key="SYNTHETIC_PROVIDER_KEY_NOT_REAL",
        model="deepseek-flash",
        prices=PRICES,
        actor_id="synthetic-admin",
    )
    billing = BillingStore(store.engine)
    billing.init()
    yield store, settings, user["id"], BillingContext.from_job(claim), billing
    store.close()


@pytest.mark.parametrize("key_source", ["admin", "environment"])
def test_actual_adapter_schema_failure_still_settles_each_http_attempt(
    metered_environment, monkeypatch, key_source
):
    from app.exam_models import ExamModelResponseError
    from app.model_runtime import MeteredExamAdapter

    store, settings, user_id, context, billing = metered_environment
    if key_source == "environment":
        from app.model_settings import ModelSettingsStore

        ModelSettingsStore(settings.data_dir).path.unlink()
        monkeypatch.setenv("DEEPSEEK_API_KEY", "SYNTHETIC_ENVIRONMENT_KEY")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    billing.top_up(user_id, "3", "schema-credit", "synthetic-admin")
    requests = []

    def provider(request):
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "not-valid-json"}}],
                "usage": USAGE,
            },
        )

    adapter = MeteredExamAdapter(
        store=store,
        settings=settings,
        context=context,
        network_factory=lambda: httpx.MockTransport(provider),
    )
    with pytest.raises(ExamModelResponseError):
        asyncio.run(adapter.assess(task="paper", prompt="synthetic request", image_paths=[]))
    assert requests == ["/chat/completions"]
    wallet = billing.get_wallet(user_id)
    assert wallet["used_credit"] == "0.002408"
    assert wallet["reserved_credit"] == "0.000000"


def test_actual_adapter_timeout_cannot_send_retry_without_a_second_reservation(metered_environment):
    from app.model_runtime import MeteredExamAdapter

    store, settings, user_id, context, billing = metered_environment
    billing.top_up(user_id, RESERVATION, "timeout-credit", "synthetic-admin")
    sent = []

    def provider(request):
        sent.append(request.url.path)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    adapter = MeteredExamAdapter(
        store=store,
        settings=settings,
        context=context,
        network_factory=lambda: httpx.MockTransport(provider),
    )
    with pytest.raises(InsufficientCredit):
        asyncio.run(adapter.assess(task="paper", prompt="synthetic request", image_paths=[]))
    assert sent == ["/chat/completions"]
    assert billing.get_wallet(user_id)["reserved_credit"] == RESERVATION
    assert len(billing.list_unresolved()) == 1


def test_owner_forgery_is_rejected_before_http_or_wallet_reservation(metered_environment):
    from app.model_runtime import BillingContext, MeteredExamAdapter, MeteringError

    store, settings, user_id, context, billing = metered_environment
    billing.top_up(user_id, "3", "owner-credit", "synthetic-admin")
    sent = []
    forged = BillingContext("another-synthetic-owner", context.job_id, context.lease_token)
    with pytest.raises(MeteringError):
        MeteredExamAdapter(
            store=store,
            settings=settings,
            context=forged,
            network_factory=lambda: httpx.MockTransport(lambda request: sent.append(request)),
        )
    assert not sent
    assert billing.get_wallet(user_id)["reserved_credit"] == "0.000000"


def test_internal_cli_missing_context_cannot_fall_back_to_environment_key(monkeypatch):
    from app.model_runtime import CONTEXT_ENV, REQUIRED_ENV, MeteringError, adapter_from_environment

    monkeypatch.setenv(REQUIRED_ENV, "1")
    monkeypatch.delenv(CONTEXT_ENV, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SYNTHETIC_ENV_KEY_MUST_NOT_BE_USED")
    with pytest.raises(MeteringError):
        adapter_from_environment()


def test_admin_api_enforces_ownership_csrf_and_write_only_key(metered_environment):
    from app.web_api import create_app
    from fastapi.testclient import TestClient

    store, settings, user_id, _context, billing = metered_environment
    store.create_user("synthetic-billing-admin", "synthetic-password", "合成管理员", role="admin")
    app = create_app(settings=settings, store=store)
    with TestClient(app) as admin, TestClient(app) as teacher:
        admin_login = admin.post(
            "/api/auth/login",
            json={"username": "synthetic-billing-admin", "password": "synthetic-password"},
        ).json()
        teacher_login = teacher.post(
            "/api/auth/login",
            json={"username": "billing-acceptance", "password": "synthetic-password"},
        ).json()
        a_csrf = {"X-CSRF-Token": admin_login["csrf_token"]}
        t_csrf = {"X-CSRF-Token": teacher_login["csrf_token"]}
        assert teacher.get("/api/admin/deepseek").status_code == 403
        assert teacher.get("/api/admin/billing/wallets").status_code == 403
        credit_url = f"/api/admin/billing/users/{user_id}/credits"
        payload = {"amount": "3", "request_id": "http-credit"}
        assert teacher.post(credit_url, json=payload, headers=t_csrf).status_code == 403
        assert admin.post(credit_url, json=payload).status_code == 403
        assert admin.post(credit_url, json=payload, headers=a_csrf).status_code == 200
        assert admin.post(credit_url, json=payload, headers=a_csrf).status_code == 200
        assert billing.get_wallet(user_id)["total_credit"] == "3.000000"
        own = teacher.get("/api/billing/me", params={"user_id": admin_login["user"]["id"]}).json()
        assert own["user_id"] == user_id
        secret = "SYNTHETIC_ADMIN_API_KEY_MUST_NOT_ECHO"
        revision = admin.get("/api/admin/deepseek").json()["revision"]
        saved = admin.post(
            "/api/admin/deepseek",
            headers=a_csrf,
            json={
                "expected_revision": revision,
                "api_key": secret,
                "model": "deepseek-flash",
                "prices": PRICES,
            },
        )
        assert saved.status_code == 200
        public = admin.get("/api/admin/deepseek")
        assert secret not in saved.text + public.text
        invalid = admin.post(
            "/api/admin/deepseek",
            headers=a_csrf,
            json={
                "expected_revision": saved.json()["revision"],
                "api_key": {"unexpected": secret},
                "model": "deepseek-flash",
                "prices": PRICES,
            },
        )
        assert invalid.status_code == 422
        assert secret not in invalid.text
