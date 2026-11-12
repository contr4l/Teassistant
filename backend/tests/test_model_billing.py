"""Synthetic tests for the estimated-credit ledger; no model calls are made."""

from datetime import UTC, datetime

import pytest
from app.model_billing import (
    MAX_INPUT_TOKENS,
    BillingConflict,
    BillingError,
    BillingStore,
    InsufficientCredit,
    _DefaultsEvent,
    _Event,
    _Wallet,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

PRICES = {"input_cache_hit": "0.04", "input_cache_miss": "2", "output": "8"}
HOLD = "2.162688"


@pytest.fixture
def ledger(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'billing.sqlite'}")
    store = BillingStore(engine)
    store.init()
    yield store
    engine.dispose()


def reserve(store, **changes):
    arguments = {
        "user_id": "synthetic-user",
        "job_id": "synthetic-job",
        "stage": "paper",
        "operation_id": "synthetic-operation",
        "attempt_no": 1,
        "model": "deepseek-flash",
        "config_revision": 1,
        "prices": dict(PRICES),
        "max_output_tokens": 8192,
    }
    arguments.update(changes)
    return store.reserve_attempt(**arguments)


def test_reservation_uses_frozen_rates_and_full_context_bound(ledger):
    assert ledger.get_wallet("synthetic-user")["total_credit"] == "0.000000"
    ledger.top_up("synthetic-user", HOLD, "credit-one", "synthetic-admin")
    row = reserve(ledger)
    assert row["max_input_tokens"] == MAX_INPUT_TOKENS
    assert row["reserved_credit"] == HOLD
    assert row["created"] is True
    assert ledger.get_wallet("synthetic-user")["remaining_credit"] == "0.000000"
    assert reserve(ledger)["created"] is False
    assert ledger.get_wallet("synthetic-user")["reserved_credit"] == HOLD


def test_environment_fallback_configuration_revision_zero_is_meterable(ledger):
    ledger.top_up("synthetic-user", HOLD, "credit-one", "synthetic-admin")
    row = reserve(ledger, config_revision=0)
    assert row["config_revision"] == 0
    assert row["reserved_credit"] == HOLD


def test_known_usage_settles_even_when_response_schema_failed_and_is_idempotent(ledger):
    ledger.top_up("synthetic-user", "3", "credit-one", "synthetic-admin")
    row = reserve(ledger)
    usage = {
        "prompt_tokens": 1000,
        "prompt_cache_hit_tokens": 200,
        "completion_tokens": 100,
        "total_tokens": 1100,
    }
    first = ledger.settle_attempt(row["id"], usage, 200, "response_schema_invalid")
    second = ledger.settle_attempt(row["id"], usage, 200, "response_schema_invalid")
    assert first["status"] == second["status"] == "settled"
    assert first["used_cost"] == "0.002408"
    wallet = ledger.get_wallet("synthetic-user")
    assert wallet["used_credit"] == "0.002408"
    assert wallet["reserved_credit"] == "0.000000"
    assert wallet["remaining_credit"] == "2.997592"
    with pytest.raises(BillingConflict):
        ledger.settle_attempt(row["id"], {**usage, "completion_tokens": 99}, 200)


def test_missing_usage_keeps_hold_and_manual_resolution_is_idempotent(ledger):
    ledger.top_up("synthetic-user", HOLD, "credit-one", "synthetic-admin")
    row = reserve(ledger)
    ledger.settle_attempt(row["id"], None, None, "timeout")
    ledger.settle_attempt(row["id"], None, None, "timeout")
    with pytest.raises(InsufficientCredit):
        reserve(ledger, attempt_no=2)
    assert ledger.get_wallet("synthetic-user")["reserved_credit"] == HOLD
    resolved = ledger.resolve_attempt(
        row["id"], "0.5", "synthetic reconciliation", "resolve-one", "synthetic-admin"
    )
    duplicate = ledger.resolve_attempt(
        row["id"], "0.5", "synthetic reconciliation", "resolve-one", "synthetic-admin"
    )
    assert resolved["status"] == duplicate["status"] == "resolved"
    assert ledger.get_wallet("synthetic-user")["used_credit"] == "0.500000"
    assert ledger.get_wallet("synthetic-user")["reserved_credit"] == "0.000000"


def test_over_limit_usage_freezes_until_all_anomalies_are_resolved(ledger):
    ledger.top_up("synthetic-user", "10", "credit-one", "synthetic-admin")
    row = reserve(ledger)
    outcome = ledger.settle_attempt(
        row["id"],
        {"prompt_tokens": 1, "completion_tokens": 8193, "total_tokens": 8194},
        200,
    )
    assert outcome["status"] == "over_limit"
    assert ledger.get_wallet("synthetic-user")["frozen"] is True
    with pytest.raises(InsufficientCredit):
        reserve(ledger, operation_id="blocked-call")
    ledger.resolve_attempt(
        row["id"], HOLD, "synthetic verified upper bound", "resolve-overflow", "synthetic-admin"
    )
    assert ledger.get_wallet("synthetic-user")["frozen"] is False


def test_prices_reject_float_and_reservation_is_ceiled_to_micro(ledger):
    ledger.top_up("synthetic-user", "1", "credit-one", "synthetic-admin")
    with pytest.raises(BillingError):
        reserve(ledger, prices={"input_cache_hit": 0.04, "input_cache_miss": "2", "output": "8"})
    row = reserve(
        ledger,
        operation_id="tiny-rate",
        prices={
            "input_cache_hit": "0.000001",
            "input_cache_miss": "0.000001",
            "output": "0.000001",
        },
        max_output_tokens=1,
    )
    assert row["reserved_credit"] == "0.000002"


def test_yesterday_summary_uses_beijing_day_and_unknown_is_not_zero(ledger, monkeypatch):
    from app import model_billing

    clock = {"now": datetime(2026, 11, 11, 15, 59, 59, tzinfo=UTC)}
    monkeypatch.setattr(model_billing, "_now", lambda: clock["now"])
    # Tracking began in fixture setup; set test calls around the Beijing day boundary.
    ledger.top_up("synthetic-user", "10", "credit-one", "synthetic-admin")
    clock["now"] = datetime(2026, 11, 11, 16, 0, 0, tzinfo=UTC)
    row = reserve(ledger)
    ledger.settle_attempt(row["id"], None, None, "timeout")
    summary = ledger.daily_summary(now=datetime(2026, 11, 13, 0, tzinfo=UTC))
    assert summary["day"] == "2026-11-12"
    assert summary["unknown_count"] == 1
    assert summary["call_count"] == 1
    assert summary["users"][0]["estimated_cost"] is None
    assert summary["users"][0]["reserved_cost"] == HOLD


def test_new_user_default_starts_at_ten_and_save_is_cas_audited(ledger):
    assert ledger.get_defaults() == {
        "currency": "CNY",
        "new_user_credit": "10.000000",
        "revision": 1,
    }
    saved = ledger.save_defaults("20.123456", 1, "synthetic-admin")
    assert saved == {
        "currency": "CNY",
        "new_user_credit": "20.123456",
        "revision": 2,
    }
    with pytest.raises(BillingConflict):
        ledger.save_defaults("8", 1, "synthetic-admin")
    zero = ledger.save_defaults("0", 2, "synthetic-admin")
    assert zero["new_user_credit"] == "0.000000"
    assert zero["revision"] == 3
    with Session(ledger.engine) as session:
        audit_count = session.scalar(select(func.count()).select_from(_DefaultsEvent))
    assert audit_count == 3  # bootstrap plus two committed admin updates


def test_new_wallet_gets_current_default_exactly_once_and_old_wallet_stays_zero(ledger):
    assert ledger.get_defaults()["new_user_credit"] == "10.000000"
    old = ledger.get_wallet("synthetic-old-user")
    assert old["total_credit"] == "0.000000"
    with Session(ledger.engine) as session, session.begin():
        old_after_init = ledger.initialize_user_wallet(session, "synthetic-old-user")
        new_first = ledger.initialize_user_wallet(session, "synthetic-new-user")
        new_again = ledger.initialize_user_wallet(session, "synthetic-new-user")
        assert old_after_init["total_credit"] == "0.000000"
        assert new_first["total_credit"] == new_again["total_credit"] == "10.000000"
    assert ledger.get_wallet("synthetic-new-user")["total_credit"] == "10.000000"
    with Session(ledger.engine) as session:
        gifts = session.scalar(
            select(func.count()).select_from(_Event).where(_Event.action == "initial_credit")
        )
    assert gifts == 1


def test_changed_defaults_apply_only_to_future_accounts_and_init_never_resets(ledger):
    ledger.save_defaults("20.123456", 1, "synthetic-admin")
    with Session(ledger.engine) as session, session.begin():
        created = ledger.initialize_user_wallet(session, "synthetic-after-save")
    assert created["total_credit"] == "20.123456"
    ledger.init()
    assert ledger.get_defaults()["new_user_credit"] == "20.123456"
    assert ledger.get_wallet("synthetic-after-save")["total_credit"] == "20.123456"


def test_zero_default_is_valid_for_new_user(ledger):
    ledger.save_defaults("0", 1, "synthetic-admin")
    with Session(ledger.engine) as session, session.begin():
        wallet = ledger.initialize_user_wallet(session, "synthetic-zero-user")
    assert wallet["total_credit"] == "0.000000"


def test_initial_gift_rolls_back_with_the_callers_transaction(ledger):
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with Session(ledger.engine) as session, session.begin():
            ledger.initialize_user_wallet(session, "synthetic-rolled-back-user")
            raise RuntimeError("synthetic failure")
    with Session(ledger.engine) as session:
        wallet = session.get(_Wallet, "synthetic-rolled-back-user")
        gifts = session.scalar(
            select(func.count())
            .select_from(_Event)
            .where(_Event.user_id == "synthetic-rolled-back-user")
        )
    assert wallet is None
    assert gifts == 0
    with Session(ledger.engine) as session, session.begin():
        retried = ledger.initialize_user_wallet(session, "synthetic-rolled-back-user")
    assert retried["total_credit"] == "10.000000"
