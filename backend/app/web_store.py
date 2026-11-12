"""SQLAlchemy persistence for the teacher web application.

This module has no process startup side effects. Call ``Store.init`` explicitly
from the API lifespan or standalone worker before using the store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.pool import StaticPool

from app.prompt_templates import MAX_PROMPT_LENGTH

_PASSWORD_ROUNDS = 310_000
_LOGIN_WINDOW = timedelta(minutes=15)
_LOGIN_LOCK = timedelta(minutes=15)
_LOGIN_FAILURE_LIMIT = 8
_JOB_MAX_ATTEMPTS = 4


class StoreError(RuntimeError):
    """Base error for persistence operations."""


class StoreConflict(StoreError):
    """The requested update conflicts with newer persistent state."""


class StoreNotFound(StoreError):
    """A requested owner-scoped record does not exist."""


class _Base(DeclarativeBase):
    pass


class _User(_Base):
    __tablename__ = "web_users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _WebSession(_Base):
    __tablename__ = "web_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("web_users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(96))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _LoginLimit(_Base):
    __tablename__ = "web_login_limits"

    username_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class _Record(_Base):
    __tablename__ = "web_records"
    __table_args__ = (Index("ix_web_records_owner_kind_parent", "owner_id", "kind", "parent_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _Job(_Base):
    __tablename__ = "web_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "idempotency_key", name="uq_web_job_idempotency"),
        Index("ix_web_jobs_status_created", "status", "created_at"),
        Index("ix_web_jobs_owner_resource", "owner_id", "resource_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(32), index=True)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=_JOB_MAX_ATTEMPTS)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _Prompt(_Base):
    __tablename__ = "web_prompts"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    active_revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _PromptRevision(_Base):
    __tablename__ = "web_prompt_revisions"
    __table_args__ = (UniqueConstraint("prompt_key", "revision", name="uq_web_prompt_revision"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    prompt_key: Mapped[str] = mapped_column(
        ForeignKey("web_prompts.key", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(32))


def _now() -> datetime:
    return datetime.now(UTC)


def _time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _uuid() -> str:
    return uuid.uuid4().hex


def _username(value: str) -> str:
    result = value.strip().casefold()
    if not result or len(result) > 120:
        raise ValueError("invalid username")
    return result


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ROUNDS)
    return f"pbkdf2_sha256${_PASSWORD_ROUNDS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, round_text, salt_hex, expected_hex = encoded.split("$")
        rounds = int(round_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= rounds <= 1_000_000 or len(salt) < 16:
            raise ValueError
    except (ValueError, TypeError):
        # Still pay a fixed hash cost for nonexistent or corrupt user records.
        hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), b"web-login-dummy", _PASSWORD_ROUNDS
        )
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def _user_dict(user: _User) -> dict[str, str]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


def _record_dict(record: _Record) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "owner_id": record.owner_id,
        "parent_id": record.parent_id,
        "data": record.data,
        "revision": record.revision,
        "created_at": _time(record.created_at),
        "updated_at": _time(record.updated_at),
    }


def _job_dict(job: _Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "owner_id": job.owner_id,
        "kind": job.kind,
        "resource_id": job.resource_id,
        "input_data": job.input_data,
        "idempotency_key": job.idempotency_key,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "lease_token": job.lease_token,
        "worker_id": job.worker_id,
        "lease_expires_at": _time(job.lease_expires_at) if job.lease_expires_at else None,
        "result": job.result,
        "error": job.error,
        "created_at": _time(job.created_at),
        "updated_at": _time(job.updated_at),
    }


class Store:
    """Persistent SQLAlchemy store; no worker is started by constructing it."""

    def __init__(self, database_url: str, *, session_hours: int = 24):
        if not database_url or not database_url.strip():
            raise ValueError("database URL is required")
        if type(session_hours) is not int or not 1 <= session_hours <= 24 * 30:
            raise ValueError("session lifetime must be between one hour and thirty days")
        self.session_hours = session_hours
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite:") else {}
        engine_options: dict[str, Any] = {}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            engine_options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(
            database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
            **engine_options,
        )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False, class_=DBSession)

    def init(self) -> None:
        if self.engine.url.get_backend_name() == "sqlite":
            database = self.engine.url.database
            if database and database != ":memory:":
                Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        _Base.metadata.create_all(self.engine)
        from app.model_billing import BillingStore

        BillingStore(self.engine).init()

    def close(self) -> None:
        self.engine.dispose()

    def create_user(
        self, username: str, password: str, display_name: str, role: str = "teacher"
    ) -> dict[str, str]:
        username = _username(username)
        display_name = display_name.strip()
        if role not in {"admin", "teacher"}:
            raise ValueError("invalid user role")
        if not password or len(password) > 1024 or not display_name or len(display_name) > 200:
            raise ValueError("invalid user details")
        user = _User(
            id=_uuid(),
            username=username,
            display_name=display_name,
            role=role,
            password_hash=_password_hash(password),
            created_at=_now(),
        )
        try:
            with self._sessions.begin() as session:
                session.add(user)
                session.flush()
                from app.model_billing import BillingStore

                BillingStore(self.engine).initialize_user_wallet(session, user.id)
        except IntegrityError:
            raise StoreConflict("username already exists") from None
        return _user_dict(user)

    def bootstrap_users(self, accounts: list[dict[str, str]]) -> dict[str, list[str]]:
        """Create a validated seed batch atomically without ever resetting users."""
        if type(accounts) is not list or not 1 <= len(accounts) <= 100:
            raise ValueError("invalid bootstrap accounts")
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        required = {"username", "password", "display_name", "role"}
        for account in accounts:
            if type(account) is not dict or set(account) != required:
                raise ValueError("invalid bootstrap accounts")
            if any(type(account[key]) is not str for key in required):
                raise ValueError("invalid bootstrap accounts")
            try:
                username = _username(account["username"])
            except ValueError:
                raise ValueError("invalid bootstrap accounts") from None
            password = account["password"]
            display_name = account["display_name"].strip()
            role = account["role"]
            try:
                username.encode("utf-8", "strict")
                password.encode("utf-8", "strict")
                display_name.encode("utf-8", "strict")
            except UnicodeEncodeError:
                raise ValueError("invalid bootstrap accounts") from None
            if (
                username in seen
                or not password
                or len(password) > 1024
                or not display_name
                or len(display_name) > 200
                or role not in {"admin", "teacher"}
            ):
                raise ValueError("invalid bootstrap accounts")
            seen.add(username)
            normalized.append(
                {
                    "username": username,
                    "password": password,
                    "display_name": display_name,
                    "role": role,
                }
            )

        password_hashes = [_password_hash(account["password"]) for account in normalized]
        now = _now()
        created: list[str] = []
        skipped: list[str] = []
        try:
            with self._sessions.begin() as session:
                if self.engine.dialect.name == "sqlite":
                    connection = session.connection()
                    driver_connection = connection.connection.driver_connection
                    if not driver_connection.in_transaction:
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                usernames = [account["username"] for account in normalized]
                existing_rows = session.scalars(
                    select(_User).where(_User.username.in_(usernames)).with_for_update()
                ).all()
                existing = {row.username: row for row in existing_rows}
                for account in normalized:
                    prior = existing.get(account["username"])
                    if prior is not None and prior.role != account["role"]:
                        raise StoreConflict("bootstrap account role conflicts")
                new_rows = []
                for account, password_hash in zip(normalized, password_hashes, strict=True):
                    prior = existing.get(account["username"])
                    if prior is not None:
                        skipped.append(account["username"])
                        continue
                    user = _User(
                        id=_uuid(),
                        username=account["username"],
                        display_name=account["display_name"],
                        role=account["role"],
                        password_hash=password_hash,
                        created_at=now,
                    )
                    session.add(user)
                    new_rows.append(user)
                    created.append(user.username)
                session.flush()
                from app.model_billing import BillingStore

                billing = BillingStore(self.engine)
                for user in new_rows:
                    billing.initialize_user_wallet(session, user.id)
        except IntegrityError:
            raise StoreConflict("bootstrap account conflict") from None
        return {"created": created, "skipped": skipped}

    def authenticate(self, username: str, password: str) -> dict[str, str] | None:
        username = username.strip().casefold()
        if not username or len(username) > 120 or len(password) > 1024:
            _verify_password(password[:1024], "invalid")
            return None
        username_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
        now = _now()
        with self._sessions.begin() as session:
            user = session.scalar(select(_User).where(_User.username == username))
            limit = session.get(_LoginLimit, username_hash)
            if limit and limit.locked_until and _aware(limit.locked_until) > now:
                _verify_password(password, user.password_hash if user else "invalid")
                return None
            if user is None or not _verify_password(password, user.password_hash):
                if limit is None or now - _aware(limit.window_started) > _LOGIN_WINDOW:
                    limit = _LoginLimit(
                        username_hash=username_hash,
                        window_started=now,
                        failures=0,
                        locked_until=None,
                    )
                    session.add(limit)
                limit.failures += 1
                if limit.failures >= _LOGIN_FAILURE_LIMIT:
                    limit.locked_until = now + _LOGIN_LOCK
                return None
            if limit:
                session.delete(limit)
            return _user_dict(user)

    def create_session(self, user_id: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = _now()
        expires = now + timedelta(hours=self.session_hours)
        with self._sessions.begin() as session:
            user = session.get(_User, user_id)
            if user is None:
                raise StoreNotFound("user not found")
            session.add(
                _WebSession(
                    token_hash=token_hash,
                    user_id=user.id,
                    csrf_token=csrf_token,
                    expires_at=expires,
                    created_at=now,
                )
            )
            safe_user = _user_dict(user)
        return {"token": token, "csrf_token": csrf_token, "user": safe_user}

    def lookup_session(self, token: str) -> dict[str, Any] | None:
        if not token or len(token) > 256:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = _now()
        with self._sessions.begin() as session:
            row = session.get(_WebSession, token_hash)
            if row is None:
                return None
            if _aware(row.expires_at) <= now:
                session.delete(row)
                return None
            user = session.get(_User, row.user_id)
            if user is None:
                session.delete(row)
                return None
            return {"user": _user_dict(user), "csrf_token": row.csrf_token}

    def delete_session(self, token: str) -> None:
        if not token or len(token) > 256:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._sessions.begin() as session:
            session.execute(delete(_WebSession).where(_WebSession.token_hash == token_hash))

    def list_users(self) -> list[dict[str, str]]:
        with self._sessions() as session:
            users = session.scalars(select(_User).order_by(_User.username)).all()
            return [_user_dict(user) for user in users]

    def list_records(
        self, kind: str, owner_id: str, parent_id: str | None = None
    ) -> list[dict[str, Any]]:
        statement = select(_Record).where(_Record.kind == kind, _Record.owner_id == owner_id)
        if parent_id is not None:
            statement = statement.where(_Record.parent_id == parent_id)
        statement = statement.order_by(_Record.created_at, _Record.id)
        with self._sessions() as session:
            return [_record_dict(row) for row in session.scalars(statement).all()]

    def get_record(
        self, kind: str, record_id: str, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        statement = select(_Record).where(_Record.kind == kind, _Record.id == record_id)
        if owner_id is not None:
            statement = statement.where(_Record.owner_id == owner_id)
        with self._sessions() as session:
            record = session.scalar(statement)
            return _record_dict(record) if record else None

    def create_record(
        self,
        kind: str,
        owner_id: str,
        data: dict[str, Any],
        parent_id: str | None = None,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_record_input(kind, owner_id, data)
        now = _now()
        record = _Record(
            id=record_id or _uuid(),
            kind=kind,
            owner_id=owner_id,
            parent_id=parent_id,
            data=data,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                session.add(record)
                session.flush()
        except IntegrityError:
            raise StoreConflict("record identifier already exists") from None
        return _record_dict(record)

    def update_record(
        self,
        kind: str,
        record_id: str,
        owner_id: str,
        data: dict[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        _validate_record_input(kind, owner_id, data)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise ValueError("expected revision must be a positive integer")
        now = _now()
        filters = [_Record.kind == kind, _Record.id == record_id, _Record.owner_id == owner_id]
        values: dict[str, Any] = {"data": data, "updated_at": now, "revision": _Record.revision + 1}
        if expected_revision is not None:
            filters.append(_Record.revision == expected_revision)
        with self._sessions.begin() as session:
            result = session.execute(update(_Record).where(*filters).values(**values))
            if result.rowcount != 1:
                exists = session.scalar(
                    select(_Record.id).where(
                        _Record.kind == kind,
                        _Record.id == record_id,
                        _Record.owner_id == owner_id,
                    )
                )
                if exists is None:
                    raise StoreNotFound("record not found")
                raise StoreConflict("record revision changed")
            record = session.scalar(
                select(_Record).where(_Record.kind == kind, _Record.id == record_id)
            )
            assert record is not None
            return _record_dict(record)

    def enqueue_job(
        self,
        owner_id: str,
        kind: str,
        resource_id: str,
        input_data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not owner_id or not kind or not resource_id or not isinstance(input_data, dict):
            raise ValueError("invalid job input")
        if idempotency_key is not None and (not idempotency_key or len(idempotency_key) > 128):
            raise ValueError("invalid job idempotency key")
        now = _now()
        with self._sessions.begin() as session:
            if idempotency_key is not None:
                existing = session.scalar(
                    select(_Job).where(
                        _Job.owner_id == owner_id,
                        _Job.kind == kind,
                        _Job.idempotency_key == idempotency_key,
                    )
                )
                if existing:
                    if existing.input_data != input_data or existing.resource_id != resource_id:
                        raise StoreConflict("idempotency key was used with different input")
                    return _job_dict(existing)
            job = _Job(
                id=_uuid(),
                owner_id=owner_id,
                kind=kind,
                resource_id=resource_id,
                input_data=input_data,
                idempotency_key=idempotency_key,
                status="queued",
                attempts=0,
                max_attempts=_JOB_MAX_ATTEMPTS,
                lease_token=None,
                worker_id=None,
                lease_expires_at=None,
                result=None,
                error=None,
                created_at=now,
                updated_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(job)
                    session.flush()
            except IntegrityError:
                if idempotency_key is None:
                    raise StoreConflict("job could not be created") from None
                existing = session.scalar(
                    select(_Job).where(
                        _Job.owner_id == owner_id,
                        _Job.kind == kind,
                        _Job.idempotency_key == idempotency_key,
                    )
                )
                if (
                    existing
                    and existing.input_data == input_data
                    and existing.resource_id == resource_id
                ):
                    return _job_dict(existing)
                raise StoreConflict("idempotency key conflicts with another input") from None
            return _job_dict(job)

    def get_job(self, job_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        statement = select(_Job).where(_Job.id == job_id)
        if owner_id is not None:
            statement = statement.where(_Job.owner_id == owner_id)
        with self._sessions() as session:
            job = session.scalar(statement)
            return _job_dict(job) if job else None

    def list_jobs(self, owner_id: str, resource_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(_Job).where(_Job.owner_id == owner_id)
        if resource_id is not None:
            statement = statement.where(_Job.resource_id == resource_id)
        statement = statement.order_by(_Job.created_at, _Job.id)
        with self._sessions() as session:
            return [_job_dict(job) for job in session.scalars(statement).all()]

    def claim_job(self, worker_id: str, lease_seconds: int = 900) -> dict[str, Any] | None:
        if (
            not worker_id
            or len(worker_id) > 200
            or type(lease_seconds) is not int
            or lease_seconds < 1
        ):
            raise ValueError("invalid worker lease")
        now = _now()
        expires = now + timedelta(seconds=lease_seconds)
        with self._sessions.begin() as session:
            expired = session.scalars(
                select(_Job).where(
                    _Job.status == "running",
                    _Job.lease_expires_at.is_not(None),
                    _Job.lease_expires_at <= now,
                )
            ).all()
            for job in expired:
                job.lease_token = None
                job.worker_id = None
                job.lease_expires_at = None
                job.updated_at = now
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                    job.error = "worker lease expired after maximum attempts"
                else:
                    job.status = "queued"
                    job.error = "previous worker lease expired; job requeued"

            candidates = session.scalars(
                select(_Job)
                .where(_Job.status == "queued", _Job.attempts < _Job.max_attempts)
                .order_by(_Job.created_at, _Job.id)
                .limit(10)
                .with_for_update(skip_locked=True)
            ).all()
            for candidate in candidates:
                token = secrets.token_hex(32)
                changed = session.execute(
                    update(_Job)
                    .where(_Job.id == candidate.id, _Job.status == "queued")
                    .values(
                        status="running",
                        attempts=_Job.attempts + 1,
                        lease_token=token,
                        worker_id=worker_id,
                        lease_expires_at=expires,
                        updated_at=now,
                        error=None,
                    )
                )
                if changed.rowcount == 1:
                    claimed = session.scalar(select(_Job).where(_Job.id == candidate.id))
                    assert claimed is not None
                    return _job_dict(claimed)
        return None

    def heartbeat_job(self, job_id: str, lease_token: str, lease_seconds: int = 900) -> bool:
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("invalid worker lease")
        now = _now()
        result = self._execute_job_update(
            job_id,
            lease_token,
            {"lease_expires_at": now + timedelta(seconds=lease_seconds), "updated_at": now},
            require_unexpired=True,
        )
        return result

    def finish_job(
        self,
        job_id: str,
        lease_token: str,
        result: dict[str, Any] | None,
        status: str = "completed",
        error: str | None = None,
        resource_update: dict[str, Any] | None = None,
        guards: list[dict[str, Any]] | None = None,
    ) -> bool:
        if status not in {"completed", "partial", "failed"}:
            raise ValueError("invalid terminal job status")
        if status == "failed" and error is None:
            raise ValueError("failed jobs need an error summary")
        if error is not None and len(error) > 2000:
            error = error[:2000]
        now = _now()
        with self._sessions.begin() as session:
            job = session.scalar(
                select(_Job)
                .where(
                    _Job.id == job_id,
                    _Job.status == "running",
                    _Job.lease_token == lease_token,
                    _Job.lease_expires_at.is_not(None),
                    _Job.lease_expires_at > now,
                )
                .with_for_update()
            )
            if job is None:
                return False
            for guard in sorted(
                guards or [],
                key=lambda item: (
                    item.get("kind", ""),
                    item.get("owner_id", ""),
                    item.get("record_id", ""),
                ),
            ):
                kind = guard.get("kind")
                record_id = guard.get("record_id")
                owner_id = guard.get("owner_id")
                revision = guard.get("revision")
                if type(revision) is not int or revision < 1:
                    raise ValueError("job guard needs a record revision")
                guarded = session.scalar(
                    select(_Record)
                    .where(
                        _Record.kind == kind,
                        _Record.id == record_id,
                        _Record.owner_id == owner_id,
                        _Record.revision == revision,
                    )
                    .with_for_update()
                )
                if guarded is None:
                    return False
            if resource_update is not None:
                kind = resource_update.get("kind")
                record_id = resource_update.get("record_id")
                owner_id = resource_update.get("owner_id")
                data = resource_update.get("data")
                expected_revision = resource_update.get("expected_revision")
                _validate_record_input(kind, owner_id, data)
                if type(expected_revision) is not int or expected_revision < 1:
                    raise ValueError("resource update needs an expected revision")
                changed = session.execute(
                    update(_Record)
                    .where(
                        _Record.kind == kind,
                        _Record.id == record_id,
                        _Record.owner_id == owner_id,
                        _Record.revision == expected_revision,
                    )
                    .values(
                        data=data,
                        revision=_Record.revision + 1,
                        updated_at=now,
                    )
                )
                if changed.rowcount != 1:
                    return False
            job.status = status
            job.result = result
            job.error = error
            job.lease_token = None
            job.worker_id = None
            job.lease_expires_at = None
            job.updated_at = now
            return True

    def _execute_job_update(
        self,
        job_id: str,
        lease_token: str,
        values: dict[str, Any],
        *,
        require_unexpired: bool,
    ) -> bool:
        now = _now()
        filters = [
            _Job.id == job_id,
            _Job.status == "running",
            _Job.lease_token == lease_token,
        ]
        if require_unexpired:
            filters.extend([_Job.lease_expires_at.is_not(None), _Job.lease_expires_at > now])
        with self._sessions.begin() as session:
            result = session.execute(update(_Job).where(*filters).values(**values))
            return result.rowcount == 1

    def ensure_prompts(self, defaults: dict[str, dict[str, str]]) -> None:
        now = _now()
        with self._sessions.begin() as session:
            for key, values in defaults.items():
                if not key or len(key) > 120:
                    raise ValueError("invalid prompt key")
                body = values.get("body", "")
                label = values.get("label", key)
                description = values.get("description", "")
                if not body.strip():
                    raise ValueError("default prompt body is empty")
                prompt = session.get(_Prompt, key)
                if prompt is not None:
                    continue
                prompt = _Prompt(
                    key=key,
                    label=label,
                    description=description,
                    active_revision=1,
                    updated_at=now,
                )
                session.add(prompt)
                session.add(
                    _PromptRevision(
                        id=_uuid(),
                        prompt_key=key,
                        revision=1,
                        body=body,
                        created_at=now,
                        created_by="system",
                    )
                )

    def list_prompts(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            prompts = session.scalars(select(_Prompt).order_by(_Prompt.key)).all()
            result = []
            for prompt in prompts:
                current = session.scalar(
                    select(_PromptRevision).where(
                        _PromptRevision.prompt_key == prompt.key,
                        _PromptRevision.revision == prompt.active_revision,
                    )
                )
                if current:
                    result.append(
                        {
                            "key": prompt.key,
                            "label": prompt.label,
                            "description": prompt.description,
                            "active_revision": prompt.active_revision,
                            "body": current.body,
                        }
                    )
            return result

    def prompt_history(self, key: str) -> list[dict[str, Any]]:
        with self._sessions() as session:
            prompt = session.get(_Prompt, key)
            if prompt is None:
                return []
            rows = session.scalars(
                select(_PromptRevision)
                .where(_PromptRevision.prompt_key == key)
                .order_by(_PromptRevision.revision)
            ).all()
            return [
                {
                    "revision": row.revision,
                    "body": row.body,
                    "created_at": _time(row.created_at),
                    "created_by": row.created_by,
                }
                for row in rows
            ]

    def save_prompt(
        self, key: str, body: str, created_by: str, expected_revision: int
    ) -> dict[str, Any]:
        body = body.strip()
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected prompt revision must be a positive integer")
        if not body or len(body) > MAX_PROMPT_LENGTH:
            raise ValueError("prompt body is empty or too large")
        now = _now()
        with self._sessions.begin() as session:
            prompt = session.get(_Prompt, key)
            if prompt is None:
                raise StoreNotFound("prompt not found")
            if prompt.active_revision != expected_revision:
                raise StoreConflict("prompt revision changed")
            new_revision = expected_revision + 1
            session.add(
                _PromptRevision(
                    id=_uuid(),
                    prompt_key=key,
                    revision=new_revision,
                    body=body,
                    created_at=now,
                    created_by=created_by,
                )
            )
            changed = session.execute(
                update(_Prompt)
                .where(_Prompt.key == key, _Prompt.active_revision == expected_revision)
                .values(active_revision=new_revision, updated_at=now)
            )
            if changed.rowcount != 1:
                raise StoreConflict("prompt revision changed")
            return {
                "key": key,
                "active_revision": new_revision,
                "body": body,
                "created_at": _time(now),
                "created_by": created_by,
            }

    def activate_prompt(
        self, key: str, revision: int, created_by: str, expected_revision: int
    ) -> dict[str, Any]:
        if (
            type(revision) is not int
            or revision < 1
            or type(expected_revision) is not int
            or expected_revision < 1
        ):
            raise ValueError("prompt revisions must be positive integers")
        now = _now()
        with self._sessions.begin() as session:
            prompt = session.get(_Prompt, key)
            if prompt is None:
                raise StoreNotFound("prompt not found")
            if prompt.active_revision != expected_revision:
                raise StoreConflict("prompt revision changed")
            source = session.scalar(
                select(_PromptRevision).where(
                    _PromptRevision.prompt_key == key,
                    _PromptRevision.revision == revision,
                )
            )
            if source is None:
                raise StoreNotFound("prompt revision not found")
            new_revision = expected_revision + 1
            body = source.body
            session.add(
                _PromptRevision(
                    id=_uuid(),
                    prompt_key=key,
                    revision=new_revision,
                    body=body,
                    created_at=now,
                    created_by=created_by,
                )
            )
            changed = session.execute(
                update(_Prompt)
                .where(_Prompt.key == key, _Prompt.active_revision == expected_revision)
                .values(active_revision=new_revision, updated_at=now)
            )
            if changed.rowcount != 1:
                raise StoreConflict("prompt revision changed")
            return {
                "key": key,
                "active_revision": new_revision,
                "body": body,
                "created_at": _time(now),
                "created_by": created_by,
            }

    def snapshot_prompts(self) -> dict[str, dict[str, Any]]:
        with self._sessions() as session:
            prompts = session.scalars(select(_Prompt).order_by(_Prompt.key)).all()
            snapshot = {}
            for prompt in prompts:
                current = session.scalar(
                    select(_PromptRevision).where(
                        _PromptRevision.prompt_key == prompt.key,
                        _PromptRevision.revision == prompt.active_revision,
                    )
                )
                if current:
                    snapshot[prompt.key] = {
                        "revision": current.revision,
                        "body": current.body,
                        "sha256": sha256(current.body.encode("utf-8")).hexdigest(),
                    }
            return snapshot


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _validate_record_input(kind: str, owner_id: str, data: dict[str, Any]) -> None:
    if not kind or len(kind) > 80 or not owner_id or len(owner_id) > 32:
        raise ValueError("invalid record identity")
    if not isinstance(data, dict):
        raise ValueError("record data must be an object")
    try:
        json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("record data must be finite JSON") from None
