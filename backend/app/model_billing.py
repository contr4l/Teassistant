"""Transactional, estimated DeepSeek usage ledger.

This module owns its SQLAlchemy metadata and can share an existing Store.engine.
It does not call a model or read configuration/secrets.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm import Session as DBSession

MICRO_PER_CNY = 1_000_000
TOKENS_PER_MILLION = 1_000_000
MAX_INPUT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = 8_192
MAX_CREDIT_MICRO = 10**15
_INITIAL_NEW_USER_CREDIT_MICRO = 10 * MICRO_PER_CNY
_BEIJING = ZoneInfo("Asia/Shanghai")
_ERROR_CODES = {
    "timeout",
    "network_error",
    "provider_unavailable",
    "provider_permanent",
    "response_envelope_invalid",
    "response_schema_invalid",
    "finish_reason",
    "empty_content",
    "process_interrupted",
    "provider_error",
    "unknown_usage",
    "usage_over_reservation",
    "usage_over_limit",
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REASON_MAX = 500


class BillingError(RuntimeError):
    """Base class with safe fixed messages suitable for API handling."""


class InsufficientCredit(BillingError):
    def __init__(self) -> None:
        super().__init__("额度不足或账户暂时冻结")


class BillingConflict(BillingError):
    def __init__(self) -> None:
        super().__init__("计费请求与已有记录冲突")


class BillingNotFound(BillingError):
    def __init__(self) -> None:
        super().__init__("计费记录不存在")


class BillingInputError(BillingError):
    def __init__(self) -> None:
        super().__init__("计费输入无效")


class _Base(DeclarativeBase):
    pass


class _Wallet(_Base):
    __tablename__ = "billing_wallets"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    total_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    used_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _Attempt(_Base):
    __tablename__ = "billing_attempts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_id", "operation_id", "attempt_no", name="uq_billing_attempt_key"
        ),
        Index("ix_billing_attempt_started", "started_at"),
        Index("ix_billing_attempt_user_started", "user_id", "started_at"),
        Index("ix_billing_attempt_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(80))
    operation_id: Mapped[str] = mapped_column(String(128))
    attempt_no: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(80))
    config_revision: Mapped[int] = mapped_column(Integer)
    prices: Mapped[dict[str, str]] = mapped_column(JSON)
    max_input_tokens: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    reserved_micro: Mapped[int] = mapped_column(BigInteger)
    held_micro: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    observed_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settlement_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution_actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class _Event(_Base):
    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("action", "actor_id", "request_id", name="uq_billing_event_request"),
        Index("ix_billing_event_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    action: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_micro: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _Metadata(_Base):
    __tablename__ = "billing_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracking_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _Defaults(_Base):
    __tablename__ = "billing_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    new_user_credit_micro: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _DefaultsEvent(_Base):
    __tablename__ = "billing_default_events"
    __table_args__ = (UniqueConstraint("action", "revision", name="uq_billing_default_revision"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    action: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(32))
    previous_micro: Mapped[int] = mapped_column(BigInteger)
    new_micro: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _begin_write(session: DBSession) -> None:
    # SQLite's legacy driver may persist a released SAVEPOINT before its first
    # real BEGIN. Start a write transaction first and serialize cross-process
    # wallet writers; Postgres relies on row locks and conditional updates.
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def _id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise BillingInputError()
    return value


def _amount_micro(value: str, *, allow_zero: bool = False) -> int:
    if (
        not isinstance(value, str)
        or len(value) > 32
        or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", value)
    ):
        raise BillingInputError()
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise BillingInputError() from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise BillingInputError()
    scaled = amount * MICRO_PER_CNY
    if scaled != scaled.to_integral_value() or scaled > MAX_CREDIT_MICRO:
        raise BillingInputError()
    return int(scaled)


def _credit(micro: int) -> str:
    return f"{Decimal(micro) / MICRO_PER_CNY:.6f}"


def _prices(prices: dict[str, Any]) -> dict[str, str]:
    required = {"input_cache_hit", "input_cache_miss", "output"}
    if not isinstance(prices, dict) or set(prices) != required:
        raise BillingInputError()
    result: dict[str, str] = {}
    for key in sorted(required):
        value = prices[key]
        if (
            not isinstance(value, str)
            or len(value) > 24
            or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", value)
        ):
            raise BillingInputError()
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise BillingInputError() from None
        if (
            not amount.is_finite()
            or amount <= 0
            or amount > Decimal("1000000")
            or max(0, -amount.as_tuple().exponent) > 6
        ):
            raise BillingInputError()
        result[key] = format(amount, "f")
    return result


def _safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value in _ERROR_CODES else "provider_error"


def _fingerprint(payload: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _wallet_dict(row: _Wallet) -> dict[str, Any]:
    total = row.total_micro
    used = row.used_micro
    reserved = row.reserved_micro
    return {
        "user_id": row.user_id,
        "total_credit": _credit(total),
        "used_credit": _credit(used),
        "reserved_credit": _credit(reserved),
        "remaining_credit": _credit(total - used - reserved),
        "frozen": row.frozen,
        "revision": row.revision,
    }


def _attempt_dict(row: _Attempt, *, created: bool | None = None) -> dict[str, Any]:
    result = {
        "id": row.id,
        "user_id": row.user_id,
        "job_id": row.job_id,
        "stage": row.stage,
        "operation_id": row.operation_id,
        "attempt_no": row.attempt_no,
        "model": row.model,
        "config_revision": row.config_revision,
        "prices": dict(row.prices),
        "max_input_tokens": row.max_input_tokens,
        "max_output_tokens": row.max_output_tokens,
        "reserved_credit": _credit(row.reserved_micro),
        "held_credit": _credit(row.held_micro),
        "status": row.status,
        "started_at": _aware(row.started_at).isoformat(),
        "settled_at": _aware(row.settled_at).isoformat() if row.settled_at else None,
        "http_status": row.http_status,
        "error_code": row.error_code,
        "usage": (
            {
                "prompt_tokens": row.input_tokens,
                "cached_tokens": row.cached_input_tokens,
                "completion_tokens": row.output_tokens,
            }
            if row.input_tokens is not None
            else None
        ),
        "observed_cost": _credit(row.observed_micro) if row.observed_micro is not None else None,
        "used_cost": _credit(row.used_micro) if row.used_micro is not None else None,
        "resolution_request_id": row.resolution_request_id,
    }
    if created is not None:
        result["created"] = created
    return result


def _extract_usage(usage: dict[str, Any] | None) -> tuple[int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    if type(input_tokens) is not int or type(output_tokens) is not int:
        return None
    if input_tokens < 0 or output_tokens < 0:
        return None
    cache_hit_candidates: list[Any] = []
    for details_key in ("prompt_tokens_details", "input_tokens_details"):
        details = usage.get(details_key)
        if details is not None:
            if not isinstance(details, dict):
                return None
            if "cached_tokens" in details:
                cache_hit_candidates.append(details["cached_tokens"])
    for hit_key in ("prompt_cache_hit_tokens", "cached_tokens", "cache_hit_tokens"):
        if hit_key in usage:
            cache_hit_candidates.append(usage[hit_key])
    if any(type(value) is not int or value < 0 for value in cache_hit_candidates):
        return None
    if cache_hit_candidates and any(
        value != cache_hit_candidates[0] for value in cache_hit_candidates[1:]
    ):
        return None
    cached = cache_hit_candidates[0] if cache_hit_candidates else 0
    if cached > input_tokens:
        return None
    if "prompt_cache_miss_tokens" in usage:
        cache_miss = usage["prompt_cache_miss_tokens"]
        if type(cache_miss) is not int or cache_miss < 0 or cache_miss != input_tokens - cached:
            return None
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != input_tokens + output_tokens):
        return None
    return input_tokens, cached, output_tokens


def _usage_cost(usage: tuple[int, int, int], prices: dict[str, str]) -> int:
    input_tokens, cached, output_tokens = usage
    hit = Decimal(prices["input_cache_hit"])
    miss = Decimal(prices["input_cache_miss"])
    output = Decimal(prices["output"])
    amount = cached * hit + (input_tokens - cached) * miss + output_tokens * output
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


class BillingStore:
    """Persist lifetime estimated-credit wallets and per-HTTP-attempt holds."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False, class_=DBSession)

    def init(self) -> None:
        _Base.metadata.create_all(self.engine)
        now = _now()
        with self._sessions.begin() as session:
            _begin_write(session)
            if session.get(_Metadata, 1) is None:
                try:
                    with session.begin_nested():
                        session.add(_Metadata(id=1, tracking_started_at=now))
                        session.flush()
                except IntegrityError:
                    pass
            self._ensure_defaults(session, now)

    def get_wallet(self, user_id: str) -> dict[str, Any]:
        user_id = _id(user_id, "user id")
        now = _now()
        with self._sessions.begin() as session:
            _begin_write(session)
            row = session.get(_Wallet, user_id)
            if row is None:
                try:
                    with session.begin_nested():
                        session.add(
                            _Wallet(
                                user_id=user_id,
                                total_micro=0,
                                used_micro=0,
                                reserved_micro=0,
                                frozen=False,
                                revision=1,
                                updated_at=now,
                            )
                        )
                        session.flush()
                except IntegrityError:
                    pass
                row = session.scalar(select(_Wallet).where(_Wallet.user_id == user_id))
            assert row is not None
            return _wallet_dict(row)

    def get_defaults(self) -> dict[str, Any]:
        with self._sessions() as session:
            row = session.get(_Defaults, 1)
            if row is None:
                raise BillingNotFound()
            return self._defaults_dict(row)

    def save_defaults(
        self, new_user_credit: str, expected_revision: int, actor_id: str
    ) -> dict[str, Any]:
        amount_micro = _amount_micro(new_user_credit, allow_zero=True)
        actor_id = _id(actor_id, "actor id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise BillingInputError()
        now = _now()
        with self._sessions.begin() as session:
            _begin_write(session)
            current = session.scalar(select(_Defaults).where(_Defaults.id == 1).with_for_update())
            if current is None or current.revision != expected_revision:
                raise BillingConflict()
            previous = current.new_user_credit_micro
            new_revision = current.revision + 1
            changed = session.execute(
                update(_Defaults)
                .where(_Defaults.id == 1, _Defaults.revision == expected_revision)
                .values(
                    new_user_credit_micro=amount_micro,
                    revision=new_revision,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise BillingConflict()
            session.add(
                _DefaultsEvent(
                    id=uuid.uuid4().hex,
                    action="admin_update",
                    actor_id=actor_id,
                    previous_micro=previous,
                    new_micro=amount_micro,
                    revision=new_revision,
                    created_at=now,
                )
            )
            row = session.get(_Defaults, 1)
            assert row is not None
            return self._defaults_dict(row)

    def initialize_user_wallet(self, session: DBSession, user_id: str) -> dict[str, Any]:
        """Create the one-time new-user wallet inside the caller's transaction."""
        user_id = _id(user_id, "user id")
        if session.get_bind() is not self.engine:
            raise BillingInputError()
        _begin_write(session)
        wallet = session.get(_Wallet, user_id)
        if wallet is not None:
            return _wallet_dict(wallet)
        now = _now()
        defaults = self._ensure_defaults(session, now, lock=True)
        credit = defaults.new_user_credit_micro
        wallet = _Wallet(
            user_id=user_id,
            total_micro=credit,
            used_micro=0,
            reserved_micro=0,
            frozen=False,
            revision=1,
            updated_at=now,
        )
        session.add(wallet)
        session.add(
            _Event(
                id=uuid.uuid4().hex,
                action="initial_credit",
                actor_id="system",
                user_id=user_id,
                request_id=None,
                attempt_id=None,
                amount_micro=credit,
                reason=f"new_user_default_revision_{defaults.revision}",
                created_at=now,
            )
        )
        session.flush()
        return _wallet_dict(wallet)

    def top_up(self, user_id: str, amount: str, request_id: str, actor_id: str) -> dict[str, Any]:
        user_id = _id(user_id, "user id")
        actor_id = _id(actor_id, "actor id")
        request_id = _id(request_id, "request id")
        amount_micro = _amount_micro(amount)
        now = _now()
        with self._sessions.begin() as session:
            _begin_write(session)
            event = _Event(
                id=uuid.uuid4().hex,
                action="top_up",
                actor_id=actor_id,
                user_id=user_id,
                request_id=request_id,
                attempt_id=None,
                amount_micro=amount_micro,
                reason=None,
                created_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(event)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(_Event).where(
                        _Event.action == "top_up",
                        _Event.actor_id == actor_id,
                        _Event.request_id == request_id,
                    )
                )
                if (
                    existing is None
                    or existing.user_id != user_id
                    or existing.amount_micro != amount_micro
                ):
                    raise BillingConflict() from None
                wallet = session.get(_Wallet, user_id)
                assert wallet is not None
                return _wallet_dict(wallet)
            self._ensure_wallet(session, user_id, now)
            changed = session.execute(
                update(_Wallet)
                .where(
                    _Wallet.user_id == user_id,
                    _Wallet.total_micro <= MAX_CREDIT_MICRO - amount_micro,
                )
                .values(
                    total_micro=_Wallet.total_micro + amount_micro,
                    revision=_Wallet.revision + 1,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise BillingInputError()
            wallet = session.get(_Wallet, user_id)
            assert wallet is not None
            return _wallet_dict(wallet)

    def reserve_attempt(
        self,
        user_id: str,
        job_id: str,
        stage: str,
        operation_id: str,
        attempt_no: int,
        model: str,
        config_revision: int,
        prices: dict[str, str],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        user_id = _id(user_id, "user id")
        job_id = _id(job_id, "job id")
        stage = _id(stage, "stage")
        operation_id = _id(operation_id, "operation id")
        model = _id(model, "model")
        if model != "deepseek-flash":
            raise BillingInputError()
        if type(attempt_no) is not int or not 1 <= attempt_no <= 5:
            raise BillingInputError()
        if type(config_revision) is not int or config_revision < 0:
            raise BillingInputError()
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise BillingInputError()
        price_snapshot = _prices(prices)
        reserve_cost = Decimal(MAX_INPUT_TOKENS) * max(
            Decimal(price_snapshot["input_cache_hit"]),
            Decimal(price_snapshot["input_cache_miss"]),
        ) + Decimal(max_output_tokens) * Decimal(price_snapshot["output"])
        reserve = int(reserve_cost.to_integral_value(rounding=ROUND_CEILING))
        if reserve <= 0 or reserve > MAX_CREDIT_MICRO:
            raise BillingInputError()
        now = _now()
        attempt_id = uuid.uuid4().hex
        with self._sessions.begin() as session:
            _begin_write(session)
            new_attempt = _Attempt(
                id=attempt_id,
                user_id=user_id,
                job_id=job_id,
                stage=stage,
                operation_id=operation_id,
                attempt_no=attempt_no,
                model=model,
                config_revision=config_revision,
                prices=price_snapshot,
                max_input_tokens=MAX_INPUT_TOKENS,
                max_output_tokens=max_output_tokens,
                reserved_micro=reserve,
                held_micro=reserve,
                status="reserved",
                started_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(new_attempt)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(_Attempt).where(
                        _Attempt.user_id == user_id,
                        _Attempt.job_id == job_id,
                        _Attempt.operation_id == operation_id,
                        _Attempt.attempt_no == attempt_no,
                    )
                )
                if existing is None or not self._reservation_matches(
                    existing,
                    stage=stage,
                    model=model,
                    config_revision=config_revision,
                    prices=price_snapshot,
                    max_output_tokens=max_output_tokens,
                    reserve=reserve,
                ):
                    raise BillingConflict() from None
                return _attempt_dict(existing, created=False)
            self._ensure_wallet(session, user_id, now)
            changed = session.execute(
                update(_Wallet)
                .where(
                    _Wallet.user_id == user_id,
                    _Wallet.frozen.is_(False),
                    _Wallet.total_micro - _Wallet.used_micro - _Wallet.reserved_micro >= reserve,
                )
                .values(
                    reserved_micro=_Wallet.reserved_micro + reserve,
                    revision=_Wallet.revision + 1,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                wallet = session.get(_Wallet, user_id)
                if wallet is None:
                    raise BillingNotFound()
                raise InsufficientCredit()
            session.add(
                _Event(
                    id=uuid.uuid4().hex,
                    action="reserve",
                    actor_id="system",
                    user_id=user_id,
                    request_id=None,
                    attempt_id=attempt_id,
                    amount_micro=reserve,
                    reason=None,
                    created_at=now,
                )
            )
            return _attempt_dict(new_attempt, created=True)

    def settle_attempt(
        self,
        attempt_id: str,
        usage: dict[str, Any] | None,
        http_status: int | None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        attempt_id = _id(attempt_id, "attempt id")
        if http_status is not None and (
            type(http_status) is not int or not 100 <= http_status <= 599
        ):
            raise BillingInputError()
        safe_error = _safe_error_code(error_code)
        parsed_usage = _extract_usage(usage)
        now = _now()
        fingerprint = _fingerprint(
            {
                "usage": list(parsed_usage) if parsed_usage is not None else None,
                "http_status": http_status,
                "error_code": safe_error,
            }
        )
        with self._sessions.begin() as session:
            _begin_write(session)
            attempt = session.scalar(
                select(_Attempt).where(_Attempt.id == attempt_id).with_for_update()
            )
            if attempt is None:
                raise BillingNotFound()
            if attempt.status in {"settled", "resolved", "over_reserved", "over_limit"}:
                if attempt.settlement_fingerprint == fingerprint:
                    return _attempt_dict(attempt)
                raise BillingConflict()
            if attempt.status == "unknown":
                if parsed_usage is None:
                    if attempt.settlement_fingerprint == fingerprint:
                        return _attempt_dict(attempt)
                    raise BillingConflict()
                # A late valid transport receipt may resolve an earlier unknown hold.
            if attempt.status not in {"reserved", "unknown"}:
                raise BillingConflict()
            attempt.http_status = http_status
            attempt.error_code = safe_error
            attempt.settled_at = now
            attempt.settlement_fingerprint = fingerprint
            wallet = session.scalar(
                select(_Wallet).where(_Wallet.user_id == attempt.user_id).with_for_update()
            )
            if wallet is None:
                raise BillingNotFound()
            if parsed_usage is None:
                if attempt.status == "reserved":
                    attempt.status = "unknown"
                session.add(
                    _Event(
                        id=uuid.uuid4().hex,
                        action="unknown_hold",
                        actor_id="system",
                        user_id=attempt.user_id,
                        request_id=f"{attempt.id}:{fingerprint}",
                        attempt_id=attempt.id,
                        amount_micro=0,
                        reason=safe_error,
                        created_at=now,
                    )
                )
                return _attempt_dict(attempt)
            input_tokens, cached_tokens, output_tokens = parsed_usage
            attempt.input_tokens = input_tokens
            attempt.cached_input_tokens = cached_tokens
            attempt.output_tokens = output_tokens
            observed = _usage_cost(parsed_usage, attempt.prices)
            attempt.observed_micro = observed
            if input_tokens > attempt.max_input_tokens or output_tokens > attempt.max_output_tokens:
                attempt.status = "over_limit"
                attempt.error_code = "usage_over_limit"
                wallet.frozen = True
                wallet.revision += 1
                wallet.updated_at = now
                session.add(
                    _Event(
                        id=uuid.uuid4().hex,
                        action="unknown_hold",
                        actor_id="system",
                        user_id=attempt.user_id,
                        request_id=f"{attempt.id}:{fingerprint}",
                        attempt_id=attempt.id,
                        amount_micro=0,
                        reason="usage_over_limit",
                        created_at=now,
                    )
                )
                return _attempt_dict(attempt)
            if observed > attempt.held_micro:
                attempt.status = "over_reserved"
                attempt.error_code = "usage_over_reservation"
                wallet.frozen = True
                wallet.revision += 1
                wallet.updated_at = now
                session.add(
                    _Event(
                        id=uuid.uuid4().hex,
                        action="unknown_hold",
                        actor_id="system",
                        user_id=attempt.user_id,
                        request_id=f"{attempt.id}:{fingerprint}",
                        attempt_id=attempt.id,
                        amount_micro=0,
                        reason="usage_over_reservation",
                        created_at=now,
                    )
                )
                return _attempt_dict(attempt)
            delta = attempt.held_micro - observed
            wallet.reserved_micro -= attempt.held_micro
            wallet.used_micro += observed
            wallet.revision += 1
            wallet.updated_at = now
            attempt.held_micro = 0
            attempt.used_micro = observed
            attempt.status = "settled"
            session.add(
                _Event(
                    id=uuid.uuid4().hex,
                    action="settlement",
                    actor_id="system",
                    user_id=attempt.user_id,
                    request_id=f"{attempt.id}:{fingerprint}",
                    attempt_id=attempt.id,
                    amount_micro=observed,
                    reason=safe_error,
                    created_at=now,
                )
            )
            if delta:
                session.add(
                    _Event(
                        id=uuid.uuid4().hex,
                        action="release",
                        actor_id="system",
                        user_id=attempt.user_id,
                        request_id=f"{attempt.id}:{fingerprint}",
                        attempt_id=attempt.id,
                        amount_micro=delta,
                        reason="reservation_excess",
                        created_at=now,
                    )
                )
            return _attempt_dict(attempt)

    def resolve_attempt(
        self,
        attempt_id: str,
        amount: str,
        reason: str,
        request_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        attempt_id = _id(attempt_id, "attempt id")
        request_id = _id(request_id, "request id")
        actor_id = _id(actor_id, "actor id")
        amount_micro = _amount_micro(amount, allow_zero=True)
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > _REASON_MAX
            or any(ord(character) < 32 and character not in "\t\n" for character in reason)
        ):
            raise BillingInputError()
        clean_reason = reason.strip()
        now = _now()
        with self._sessions.begin() as session:
            _begin_write(session)
            event = _Event(
                id=uuid.uuid4().hex,
                action="resolve",
                actor_id=actor_id,
                user_id="",
                request_id=request_id,
                attempt_id=attempt_id,
                amount_micro=amount_micro,
                reason=clean_reason,
                created_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(event)
                    session.flush()
            except IntegrityError:
                existing_event = session.scalar(
                    select(_Event).where(
                        _Event.action == "resolve",
                        _Event.actor_id == actor_id,
                        _Event.request_id == request_id,
                    )
                )
                if (
                    existing_event is None
                    or existing_event.attempt_id != attempt_id
                    or existing_event.amount_micro != amount_micro
                    or existing_event.reason != clean_reason
                ):
                    raise BillingConflict() from None
                attempt = session.get(_Attempt, attempt_id)
                if attempt is None:
                    raise BillingNotFound()
                return _attempt_dict(attempt)
            attempt = session.scalar(
                select(_Attempt).where(_Attempt.id == attempt_id).with_for_update()
            )
            if attempt is None:
                raise BillingNotFound()
            if attempt.status not in {"unknown", "over_reserved", "over_limit"}:
                raise BillingConflict()
            if amount_micro > attempt.reserved_micro or amount_micro > attempt.held_micro:
                raise BillingInputError()
            wallet = session.scalar(
                select(_Wallet).where(_Wallet.user_id == attempt.user_id).with_for_update()
            )
            if wallet is None:
                raise BillingNotFound()
            event.user_id = attempt.user_id
            wallet.reserved_micro -= attempt.held_micro
            wallet.used_micro += amount_micro
            wallet.revision += 1
            wallet.updated_at = now
            attempt.held_micro = 0
            attempt.used_micro = amount_micro
            attempt.status = "resolved"
            attempt.settled_at = now
            attempt.resolution_request_id = request_id
            attempt.resolution_actor_id = actor_id
            attempt.resolution_reason = clean_reason
            self._refresh_frozen(session, wallet)
            release = attempt.reserved_micro - amount_micro
            if release:
                session.add(
                    _Event(
                        id=uuid.uuid4().hex,
                        action="release",
                        actor_id=actor_id,
                        user_id=attempt.user_id,
                        request_id=f"{request_id}:release",
                        attempt_id=attempt_id,
                        amount_micro=release,
                        reason="manual_resolution_excess",
                        created_at=now,
                    )
                )
            return _attempt_dict(attempt)

    def daily_summary(self, day: date | None = None, now: datetime | None = None) -> dict[str, Any]:
        current = _aware(now or _now())
        if day is None:
            day = current.astimezone(_BEIJING).date() - timedelta(days=1)
        if not isinstance(day, date) or isinstance(day, datetime):
            raise BillingInputError()
        local_start = datetime.combine(day, time.min, tzinfo=_BEIJING)
        local_end = local_start + timedelta(days=1)
        start = local_start.astimezone(UTC)
        end = local_end.astimezone(UTC)
        with self._sessions() as session:
            meta = session.get(_Metadata, 1)
            tracking = _aware(meta.tracking_started_at) if meta else current
            rows = session.scalars(
                select(_Attempt).where(
                    _Attempt.started_at >= start,
                    _Attempt.started_at < end,
                )
            ).all()
        if tracking >= end:
            coverage = "not_tracked"
        elif tracking > start:
            coverage = "partial"
        else:
            coverage = "tracked"
        grouped: dict[str, list[_Attempt]] = {}
        for row in rows:
            grouped.setdefault(row.user_id, []).append(row)
        users: list[dict[str, Any]] = []
        total_known = 0
        unknown_total = 0
        call_total = 0
        for user_id, user_rows in grouped.items():
            known = sum(
                row.used_micro or 0 for row in user_rows if row.status in {"settled", "resolved"}
            )
            unknown = sum(
                row.status in {"reserved", "unknown", "over_reserved", "over_limit"}
                for row in user_rows
            )
            held = sum(row.held_micro for row in user_rows)
            row_summary = {
                "user_id": user_id,
                "known_cost": _credit(known),
                "estimated_cost": None if unknown else _credit(known),
                "call_count": len(user_rows),
                "unknown_count": unknown,
                "reserved_cost": _credit(held),
            }
            users.append(row_summary)
            total_known += known
            unknown_total += unknown
            call_total += len(user_rows)
        users.sort(
            key=lambda item: (-int(Decimal(item["known_cost"]) * MICRO_PER_CNY), item["user_id"])
        )
        return {
            "day": day.isoformat(),
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "tracking_started_at": tracking.isoformat(),
            "coverage": coverage,
            "known_cost": _credit(total_known),
            "unknown_count": unknown_total,
            "call_count": call_total,
            "users": users,
        }

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        attempt_id = _id(attempt_id, "attempt id")
        with self._sessions() as session:
            row = session.get(_Attempt, attempt_id)
            if row is None:
                raise BillingNotFound()
            return _attempt_dict(row)

    def list_unresolved(self, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise BillingInputError()
        with self._sessions() as session:
            rows = session.scalars(
                select(_Attempt)
                .where(_Attempt.status.in_(["reserved", "unknown", "over_reserved", "over_limit"]))
                .order_by(_Attempt.started_at, _Attempt.id)
                .limit(limit)
            ).all()
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "job_id": row.job_id,
                "stage": row.stage,
                "operation_id": row.operation_id,
                "attempt_no": row.attempt_no,
                "started_at": _aware(row.started_at).isoformat(),
                "reserved_credit": _credit(row.held_micro),
                "error_code": row.error_code,
                "status": row.status,
            }
            for row in rows
        ]

    @staticmethod
    def _reservation_matches(
        row: _Attempt,
        *,
        stage: str,
        model: str,
        config_revision: int,
        prices: dict[str, str],
        max_output_tokens: int,
        reserve: int,
    ) -> bool:
        return (
            row.stage == stage
            and row.model == model
            and row.config_revision == config_revision
            and row.prices == prices
            and row.max_output_tokens == max_output_tokens
            and row.reserved_micro == reserve
        )

    @staticmethod
    def _ensure_wallet(session: DBSession, user_id: str, now: datetime) -> _Wallet:
        row = session.get(_Wallet, user_id)
        if row is not None:
            return row
        try:
            with session.begin_nested():
                session.add(
                    _Wallet(
                        user_id=user_id,
                        total_micro=0,
                        used_micro=0,
                        reserved_micro=0,
                        frozen=False,
                        revision=1,
                        updated_at=now,
                    )
                )
                session.flush()
        except IntegrityError:
            pass
        row = session.get(_Wallet, user_id)
        if row is None:
            raise BillingNotFound()
        return row

    @staticmethod
    def _defaults_dict(row: _Defaults) -> dict[str, Any]:
        return {
            "currency": "CNY",
            "new_user_credit": _credit(row.new_user_credit_micro),
            "revision": row.revision,
        }

    @staticmethod
    def _ensure_defaults(session: DBSession, now: datetime, *, lock: bool = False) -> _Defaults:
        statement = select(_Defaults).where(_Defaults.id == 1)
        if lock:
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is not None:
            return row
        try:
            with session.begin_nested():
                row = _Defaults(
                    id=1,
                    new_user_credit_micro=_INITIAL_NEW_USER_CREDIT_MICRO,
                    revision=1,
                    updated_at=now,
                )
                session.add(row)
                session.add(
                    _DefaultsEvent(
                        id=uuid.uuid4().hex,
                        action="bootstrap",
                        actor_id="system",
                        previous_micro=0,
                        new_micro=_INITIAL_NEW_USER_CREDIT_MICRO,
                        revision=1,
                        created_at=now,
                    )
                )
                session.flush()
        except IntegrityError:
            pass
        row = session.scalar(statement)
        if row is None:
            raise BillingNotFound()
        return row

    @staticmethod
    def _refresh_frozen(session: DBSession, wallet: _Wallet) -> None:
        has_overage = session.scalar(
            select(_Attempt.id)
            .where(
                _Attempt.user_id == wallet.user_id,
                _Attempt.status.in_(["over_reserved", "over_limit"]),
            )
            .limit(1)
        )
        wallet.frozen = has_overage is not None
