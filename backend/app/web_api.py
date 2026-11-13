"""Teacher-facing private web API. Creating the app never starts a worker."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path, PurePath
from typing import Annotated, Any, Literal

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, ValidationError

from app.contracts import Curriculum, PracticeRequest, Quota
from app.curriculum import CurriculumIndex
from app.exam_contracts import ExamAnalysis, ExamCorrection, ExamIntake
from app.model_billing import (
    BillingConflict,
    BillingError,
    BillingNotFound,
    BillingStore,
    InsufficientCredit,
)
from app.model_settings import (
    ModelSettingsConflict,
    ModelSettingsError,
    ModelSettingsStore,
    reservation_credit,
)
from app.prompt_templates import DEFAULT_PROMPTS, MAX_PROMPT_LENGTH
from app.web_settings import WebSettings
from app.web_store import Store, StoreConflict, StoreNotFound

_ID = re.compile(r"^[a-f0-9]{32}$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")
_IMAGE_TYPES = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png")}
_PasswordInput = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1024, strip_whitespace=False),
]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Login(_Input):
    username: str = Field(min_length=1, max_length=120)
    password: _PasswordInput


class _StudentCreate(_Input):
    name: str = Field(min_length=1, max_length=200)
    school: str = Field(default="", max_length=200)
    grade: Literal[7, 8, 9]
    learned_point_ids: list[str] = Field(default_factory=list)


class _StudentPatch(_Input):
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    school: str | None = Field(default=None, max_length=200)
    grade: Literal[7, 8, 9] | None = None
    learned_point_ids: list[str] | None = None


class _ExamCreate(_Input):
    student_id: str
    title: str = Field(min_length=1, max_length=200)
    exam_date: date | None = None
    question_count: int = Field(ge=1, le=200, strict=True)
    class_rank: int | None = Field(default=None, ge=1, strict=True)
    grade_rank: int | None = Field(default=None, ge=1, strict=True)
    total_score: float | None = Field(
        default=None, ge=0, le=10000, strict=True, allow_inf_nan=False
    )
    max_score: float | None = Field(default=None, gt=0, le=10000, strict=True, allow_inf_nan=False)


class _Order(_Input):
    asset_ids: list[str] = Field(min_length=1, max_length=60)


class _Analyze(_Input):
    provider: Literal["deepseek", "codex"]


class _Corrections(_Input):
    corrections: list[dict[str, Any]] = Field(min_length=1, max_length=200)


class _PracticeCreate(_Input):
    student_id: str
    exam_ids: list[str] = Field(default_factory=list, max_length=100)
    quotas: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    seed: int = Field(default=0, strict=True)
    layout: Literal["answer_space", "compact"]
    pdf_engine: Literal["latex", "word"] = "latex"


class _Feedback(_Input):
    expectation: Literal["yes", "partial", "no"]
    quality: int = Field(ge=1, le=5, strict=True)
    saved_minutes: int = Field(ge=-100_000, le=100_000, strict=True)
    reasons: list[str] = Field(default_factory=list, max_length=30)
    comment: str = Field(default="", max_length=5000)


class _UserCreate(_Input):
    username: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=200)
    password: _PasswordInput
    role: Literal["teacher"] = "teacher"


class _PromptSave(_Input):
    body: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    expected_revision: int = Field(ge=1, strict=True)


class _PromptActivate(_Input):
    revision: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=1, strict=True)


class _ModelSave(_Input):
    expected_revision: int = Field(ge=0, strict=True)
    api_key: SecretStr | None = None
    model: str = Field(max_length=100)
    prices: dict[str, str]


class _CreditAdd(_Input):
    amount: str = Field(max_length=32)
    request_id: str = Field(min_length=1, max_length=128)


class _CreditDefaultsSave(_Input):
    new_user_credit: str = Field(max_length=32)
    expected_revision: int = Field(ge=1, strict=True)


class _ResolveCredit(_CreditAdd):
    reason: str = Field(min_length=1, max_length=500)


def _http_error(status: int, detail: str):
    raise HTTPException(status_code=status, detail=detail)


def _load_curriculum(settings: WebSettings) -> Curriculum:
    try:
        return Curriculum.model_validate_json(Path(settings.curriculum_path).read_bytes())
    except (OSError, ValidationError):
        _http_error(503, "curriculum is unavailable")


def _point_ids(values: list[str], curriculum: Curriculum) -> None:
    if len(values) != len(set(values)):
        _http_error(422, "curriculum point identifiers must be unique")
    try:
        CurriculumIndex(curriculum).validate_ids(values)
    except ValueError:
        _http_error(422, "unknown curriculum point")


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _http_error(404, "file not found")
    item = Path(relative)
    if item.is_absolute() or any(part in {"", ".", ".."} for part in PurePath(relative).parts):
        _http_error(404, "file not found")
    try:
        real_root = root.resolve(strict=True)
        candidate = real_root.joinpath(*PurePath(relative).parts)
        if candidate.is_symlink():
            _http_error(404, "file not found")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(real_root):
            _http_error(404, "file not found")
        return resolved
    except OSError:
        _http_error(404, "file not found")


def _safe_directory(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _http_error(404, "directory not found")
    item = PurePath(relative)
    if item.is_absolute() or any(part in {"", ".", ".."} for part in item.parts):
        _http_error(404, "directory not found")
    try:
        real_root = root.resolve(strict=True)
        candidate = real_root.joinpath(*item.parts).resolve(strict=True)
        if not candidate.is_dir() or not candidate.is_relative_to(real_root):
            _http_error(404, "directory not found")
        return candidate
    except OSError:
        _http_error(404, "directory not found")


def _asset_view(record: dict[str, Any]) -> dict[str, Any]:
    data = record["data"]
    return {
        "id": record["id"],
        "role": data["role"],
        "filename": data["filename"],
        "ordinal": data["ordinal"],
        "size": data["size"],
        "mime": data["mime"],
    }


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: job.get(key)
        for key in (
            "id",
            "kind",
            "resource_id",
            "status",
            "error",
            "result",
            "created_at",
            "updated_at",
            "attempts",
        )
    }


def _job_input(
    store: Store, settings: WebSettings, kind: str, resource: dict[str, Any]
) -> dict[str, Any]:
    try:
        from app.web_jobs import JobInputError, build_job_input
    except ImportError:
        _http_error(503, "job service is unavailable")
    try:
        value = build_job_input(store, settings, kind, resource)
    except JobInputError as exc:
        message = str(exc)
        lowered = message.lower()
        status = (
            409
            if any(word in lowered for word in ("revision", "changed", "更新", "已变化"))
            else 422
        )
        _http_error(status, message)
    except ValueError as exc:
        message = str(exc).lower()
        _http_error(
            409 if any(word in message for word in ("revision", "changed", "stale")) else 422,
            "job input is not ready",
        )
    except OSError:
        _http_error(409, "job source files are unavailable; refresh and retry")
    if not isinstance(value, dict):
        _http_error(503, "job service returned invalid input")
    return value


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _inspect_upload(data: bytes) -> tuple[str, str]:
    if data.startswith(b"%PDF-"):
        try:
            import pypdfium2

            pdf = pypdfium2.PdfDocument(data)
            try:
                if len(pdf) < 1:
                    raise ValueError
            finally:
                pdf.close()
        except Exception:
            _http_error(415, "uploaded PDF is invalid")
        return "application/pdf", ".pdf"
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            image.verify()
    except Exception:
        _http_error(415, "uploaded file is not a supported image or PDF")
    if image_format not in _IMAGE_TYPES:
        _http_error(415, "only JPEG, PNG and PDF are supported")
    return _IMAGE_TYPES[image_format]


def _read_private_json(settings: WebSettings, relative: str) -> dict[str, Any]:
    path = _safe_path(Path(settings.data_dir), relative)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _http_error(409, "private result is unavailable")
    if not isinstance(raw, dict):
        _http_error(409, "private result is unavailable")
    return raw


def create_app(settings: WebSettings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or WebSettings.from_env()
    store = store or Store(settings.database_url, session_hours=settings.session_hours)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        store.init()
        store.ensure_prompts(DEFAULT_PROMPTS)
        yield

    app = FastAPI(title="Teassistant teacher web", version="1", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    billing = BillingStore(store.engine)
    model_settings = ModelSettingsStore(settings.data_dir)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(_request, exc):
        # Pydantic's default error payload can echo a whole body containing a
        # credential. Preserve actionable locations without values or context.
        return Response(
            content=json.dumps(
                {
                    "detail": [
                        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                        for error in exc.errors()
                    ]
                }
            ),
            status_code=422,
            media_type="application/json",
        )

    @app.exception_handler(ModelSettingsError)
    async def model_settings_error(_request, exc):
        return Response(
            content=json.dumps({"detail": str(exc)}, ensure_ascii=False),
            status_code=409 if isinstance(exc, ModelSettingsConflict) else 422,
            media_type="application/json",
        )

    @app.exception_handler(BillingError)
    async def billing_error(_request, exc):
        status = (
            402
            if isinstance(exc, InsufficientCredit)
            else 409
            if isinstance(exc, BillingConflict)
            else 404
            if isinstance(exc, BillingNotFound)
            else 422
        )
        return Response(
            content=json.dumps({"detail": str(exc)}, ensure_ascii=False),
            status_code=status,
            media_type="application/json",
        )

    @app.exception_handler(StoreConflict)
    async def conflict_handler(_request: Request, _exc: StoreConflict):
        return Response(
            content=json.dumps({"detail": "the resource changed; reload and retry"}),
            status_code=409,
            media_type="application/json",
        )

    def current_identity(request: Request) -> dict[str, Any]:
        token = request.cookies.get(settings.cookie_name)
        identity = store.lookup_session(token or "")
        if identity is None:
            _http_error(401, "authentication required")
        request.state.web_identity = identity
        return identity

    def write_identity(
        identity: dict[str, Any] = Depends(current_identity),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict[str, Any]:
        if not csrf_token or not hmac.compare_digest(csrf_token, identity["csrf_token"]):
            _http_error(403, "CSRF token is invalid")
        return identity

    def admin_identity(identity: dict[str, Any] = Depends(current_identity)) -> dict[str, Any]:
        if identity["user"]["role"] != "admin":
            _http_error(403, "administrator access required")
        return identity

    def write_admin_identity(identity: dict[str, Any] = Depends(write_identity)) -> dict[str, Any]:
        if identity["user"]["role"] != "admin":
            _http_error(403, "administrator access required")
        return identity

    def owner_record(kind: str, record_id: str, owner_id: str) -> dict[str, Any]:
        record = store.get_record(kind, record_id, owner_id=owner_id)
        if record is None:
            _http_error(404, "resource not found")
        return record

    def student_view(record: dict[str, Any]) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "name": data["name"],
            "school": data["school"],
            "grade": data["grade"],
            "learned_point_ids": data["learned_point_ids"],
            "progress_revision": record["revision"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }

    def exam_view(record: dict[str, Any]) -> dict[str, Any]:
        data = record["data"]
        assets = store.list_records("asset", record["owner_id"], parent_id=record["id"])
        jobs = store.list_jobs(record["owner_id"], resource_id=record["id"])
        relevant = [job for job in jobs if job["kind"] in {"analysis", "correction"}]
        latest_job = relevant[-1] if relevant else None
        return {
            "id": record["id"],
            "student_id": data["student_id"],
            "title": data["title"],
            "exam_date": data["exam_date"],
            "question_count": data["question_count"],
            "class_rank": data["class_rank"],
            "grade_rank": data["grade_rank"],
            "total_score": data["total_score"],
            "max_score": data["max_score"],
            "assets": [_asset_view(asset) for asset in assets],
            "analysis_job": _job_public(latest_job) if latest_job else None,
            "analysis_summary": data.get("analysis_summary"),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }

    def practice_view(record: dict[str, Any]) -> dict[str, Any]:
        data = record["data"]
        student = store.get_record("student", data.get("student_id", ""), record["owner_id"])
        frozen_progress = data.get("progress_revision")
        progress_stale = (
            student is None
            or type(frozen_progress) not in {str, int}
            or str(frozen_progress) != str(student["revision"])
        )
        files = []
        for item in [] if progress_stale else data.get("files", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            if not _SAFE_FILE.fullmatch(name):
                continue
            files.append(
                {
                    "id": item.get("id", name),
                    "label": item.get("label", name),
                    "url": f"/api/practices/{record['id']}/files/{name}",
                }
            )
        feedback_rows = store.list_records("feedback", record["owner_id"], parent_id=record["id"])
        feedback = feedback_rows[-1]["data"] if feedback_rows else data.get("feedback")
        if feedback and feedback.get("practice_revision") != record["revision"]:
            feedback = None
        jobs = store.list_jobs(record["owner_id"], resource_id=record["id"])
        latest = jobs[-1] if jobs else None
        status = data.get("status")
        if status == "queued" and latest is not None:
            status = latest["status"]
        warnings = list(data.get("warnings", []))
        if progress_stale:
            warnings.append("学生教学进度已变化或未绑定，请按当前进度重新组卷后下载。")
        return {
            "id": record["id"],
            "student_id": data["student_id"],
            "status": status,
            "job_id": latest["id"] if latest else None,
            "snapshot": data.get("snapshot"),
            "files": files,
            "warnings": warnings,
            "feedback": feedback,
            "preview": data.get("preview"),
        }

    @app.post("/api/auth/login")
    def login(payload: _Login, response: Response):
        user = store.authenticate(payload.username, payload.password)
        if user is None:
            _http_error(401, "invalid username or password")
        session = store.create_session(user["id"])
        response.set_cookie(
            settings.cookie_name,
            session["token"],
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            max_age=settings.session_hours * 3600,
            path="/",
        )
        return {"user": user, "csrf_token": session["csrf_token"]}

    @app.get("/api/auth/me")
    def me(identity: dict[str, Any] = Depends(current_identity)):
        return identity

    @app.post("/api/auth/logout", status_code=204)
    def logout(
        request: Request,
        response: Response,
        identity: dict[str, Any] = Depends(write_identity),
    ):
        token = request.cookies.get(settings.cookie_name)
        store.delete_session(token or "")
        response.delete_cookie(
            settings.cookie_name,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        response.status_code = 204
        return response

    @app.get("/api/capabilities")
    def capabilities(_identity: dict[str, Any] = Depends(current_identity)):
        from app.admin_bank import effective_settings

        active = effective_settings(store, settings)
        return {
            "allow_codex": bool(settings.allow_codex),
            "bank_configured": bool(active.bank_dir and active.semantic_dir),
        }

    @app.get("/api/curriculum")
    def curriculum(_identity: dict[str, Any] = Depends(current_identity)):
        current = _load_curriculum(settings)
        return {
            "version": current.version,
            "points": [point.model_dump(mode="json") for point in current.points],
        }

    @app.get("/api/students")
    def students(identity: dict[str, Any] = Depends(current_identity)):
        records = store.list_records("student", identity["user"]["id"])
        return [student_view(record) for record in records]

    @app.post("/api/students", status_code=201)
    def create_student(payload: _StudentCreate, identity: dict[str, Any] = Depends(write_identity)):
        current = _load_curriculum(settings)
        _point_ids(payload.learned_point_ids, current)
        if payload.learned_point_ids:
            required = CurriculumIndex(current).required(payload.learned_point_ids)
            if not required <= set(payload.learned_point_ids):
                _http_error(422, "learned progress omits prerequisites")
        record = store.create_record(
            "student",
            identity["user"]["id"],
            {
                "name": payload.name,
                "school": payload.school,
                "grade": payload.grade,
                "learned_point_ids": payload.learned_point_ids,
            },
        )
        return student_view(record)

    @app.get("/api/students/{student_id}")
    def get_student(student_id: str, identity: dict[str, Any] = Depends(current_identity)):
        return student_view(owner_record("student", student_id, identity["user"]["id"]))

    @app.patch("/api/students/{student_id}")
    def update_student(
        student_id: str,
        payload: _StudentPatch,
        identity: dict[str, Any] = Depends(write_identity),
    ):
        owner_id = identity["user"]["id"]
        current = owner_record("student", student_id, owner_id)
        data = dict(current["data"])
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if any(value is None for value in changes.values()):
            _http_error(422, "student fields cannot be cleared with null")
        if "learned_point_ids" in changes and changes["learned_point_ids"] is not None:
            curriculum_data = _load_curriculum(settings)
            learned = changes["learned_point_ids"]
            _point_ids(learned, curriculum_data)
            required = CurriculumIndex(curriculum_data).required(learned) if learned else set()
            if not required <= set(learned):
                _http_error(422, "learned progress omits prerequisites")
        data.update(changes)
        updated = store.update_record(
            "student",
            student_id,
            owner_id,
            data,
            expected_revision=payload.expected_revision,
        )
        return student_view(updated)

    @app.get("/api/students/{student_id}/exams")
    def student_exams(student_id: str, identity: dict[str, Any] = Depends(current_identity)):
        owner_id = identity["user"]["id"]
        owner_record("student", student_id, owner_id)
        return [
            exam_view(record)
            for record in store.list_records("exam", owner_id, parent_id=student_id)
        ]

    @app.post("/api/exams", status_code=201)
    def create_exam(payload: _ExamCreate, identity: dict[str, Any] = Depends(write_identity)):
        owner_id = identity["user"]["id"]
        owner_record("student", payload.student_id, owner_id)
        if (
            payload.total_score is not None
            and payload.max_score is not None
            and payload.total_score > payload.max_score
        ):
            _http_error(422, "total score exceeds maximum score")
        record = store.create_record(
            "exam",
            owner_id,
            {
                **payload.model_dump(mode="json"),
                "analysis_path": None,
                "analysis_summary": None,
                "teacher_report": None,
            },
            parent_id=payload.student_id,
        )
        return exam_view(record)

    @app.get("/api/exams/{exam_id}")
    def get_exam(exam_id: str, identity: dict[str, Any] = Depends(current_identity)):
        return exam_view(owner_record("exam", exam_id, identity["user"]["id"]))

    @app.post("/api/exams/{exam_id}/assets", status_code=201)
    async def upload_exam_asset(
        exam_id: str,
        role: Literal["paper", "response", "reference"] = Form(...),
        file: UploadFile = File(...),
        identity: dict[str, Any] = Depends(write_identity),
    ):
        owner_id = identity["user"]["id"]
        owner_record("exam", exam_id, owner_id)
        jobs = store.list_jobs(owner_id, resource_id=exam_id)
        if any(
            job["kind"] in {"analysis", "correction"} and job["status"] in {"queued", "running"}
            for job in jobs
        ):
            _http_error(409, "exam materials are locked while processing")
        existing = store.list_records("asset", owner_id, parent_id=exam_id)
        if len(existing) >= settings.max_exam_files:
            _http_error(413, "exam file count limit reached")
        content = await file.read(settings.max_upload_bytes + 1)
        if not content or len(content) > settings.max_upload_bytes:
            _http_error(413, "uploaded file exceeds size limit")
        mime, suffix = _inspect_upload(content)
        original_name = (
            Path((file.filename or "upload").replace("\\", "/")).name.replace("\x00", "")[:200]
            or "upload"
        )
        asset_id = uuid.uuid4().hex
        relative = f"assets/{owner_id}/{exam_id}/{asset_id}{suffix}"
        data_root = Path(settings.data_dir).resolve()
        destination = data_root / relative
        if not destination.resolve().is_relative_to(data_root):
            _http_error(400, "unsafe upload destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            with temporary.open("xb") as output:
                output.write(content)
            os.replace(temporary, destination)
            record = store.create_record(
                "asset",
                owner_id,
                {
                    "path": relative,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "role": role,
                    "ordinal": len(existing),
                    "filename": original_name,
                    "size": len(content),
                    "mime": mime,
                },
                parent_id=exam_id,
                record_id=asset_id,
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        return _asset_view(record)

    @app.post("/api/exams/{exam_id}/assets/order")
    def order_exam_assets(
        exam_id: str,
        payload: _Order,
        identity: dict[str, Any] = Depends(write_identity),
    ):
        owner_id = identity["user"]["id"]
        owner_record("exam", exam_id, owner_id)
        jobs = store.list_jobs(owner_id, resource_id=exam_id)
        if any(
            job["kind"] in {"analysis", "correction"} and job["status"] in {"queued", "running"}
            for job in jobs
        ):
            _http_error(409, "exam materials are locked while processing")
        assets = store.list_records("asset", owner_id, parent_id=exam_id)
        by_id = {asset["id"]: asset for asset in assets}
        if len(payload.asset_ids) != len(set(payload.asset_ids)) or set(payload.asset_ids) != set(
            by_id
        ):
            _http_error(422, "asset order must contain every exam file exactly once")
        ordered = []
        for ordinal, asset_id in enumerate(payload.asset_ids):
            record = by_id[asset_id]
            data = dict(record["data"])
            data["ordinal"] = ordinal
            ordered.append(
                store.update_record("asset", asset_id, owner_id, data, record["revision"])
            )
        return [_asset_view(record) for record in ordered]

    @app.get("/api/assets/{asset_id}/content")
    def asset_content(asset_id: str, identity: dict[str, Any] = Depends(current_identity)):
        asset = owner_record("asset", asset_id, identity["user"]["id"])
        path = _safe_path(Path(settings.data_dir), asset["data"]["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset["data"]["sha256"]:
            _http_error(404, "file not found")
        return FileResponse(
            path, media_type=asset["data"]["mime"], filename=asset["data"]["filename"]
        )

    @app.post("/api/exams/{exam_id}/analyze", status_code=202)
    def analyze_exam(
        exam_id: str,
        payload: _Analyze,
        identity: dict[str, Any] = Depends(write_identity),
    ):
        owner_id = identity["user"]["id"]
        exam = owner_record("exam", exam_id, owner_id)
        if payload.provider == "codex" and not settings.allow_codex:
            _http_error(403, "Codex development analysis is disabled")
        if payload.provider == "deepseek" and settings.model_provider != "deepseek":
            _http_error(409, "configured business provider is unavailable")
        if payload.provider == "deepseek" and model_settings.public()["key_configured"]:
            wallet = billing.get_wallet(owner_id)
            if wallet["frozen"]:
                raise InsufficientCredit()
            from decimal import Decimal

            if Decimal(wallet["remaining_credit"]) < Decimal(
                reservation_credit(model_settings.public()["prices"])
            ):
                raise InsufficientCredit()
        assets = store.list_records("asset", owner_id, parent_id=exam_id)
        roles = {asset["data"]["role"] for asset in assets}
        if not {"paper", "response"} <= roles:
            _http_error(409, "paper and response materials are required")
        base = _job_input(store, settings, "analysis", exam)
        base["provider"] = payload.provider
        prompt_snapshot = store.snapshot_prompts()
        idempotency = _hash_json(
            {"kind": "analysis", "exam": exam_id, "input": base, "prompts": prompt_snapshot}
        )
        job = store.enqueue_job(owner_id, "analysis", exam_id, base, idempotency_key=idempotency)
        return _job_public(job)

    @app.get("/api/exams/{exam_id}/analysis")
    def get_analysis(exam_id: str, identity: dict[str, Any] = Depends(current_identity)):
        exam = owner_record("exam", exam_id, identity["user"]["id"])
        data = exam["data"]
        if not data.get("analysis_path"):
            _http_error(409, "analysis is not available yet")
        analysis = _read_private_json(settings, data["analysis_path"])
        teacher_report = data.get("teacher_report")
        if teacher_report is None and data.get("teacher_report_path"):
            teacher_report = _read_private_json(settings, data["teacher_report_path"])
        pages = []
        if data.get("intake_path") and data.get("asset_root"):
            intake = ExamIntake.model_validate_json(
                _safe_path(Path(settings.data_dir), data["intake_path"]).read_bytes()
            )
            pages = [
                {
                    "page_id": page.id,
                    "role": page.role,
                    "parent_page_id": page.parent_page_id,
                    "parent_bbox": page.parent_bbox,
                    "url": f"/api/exams/{exam_id}/analysis/pages/{page.id}/content",
                }
                for page in intake.pages
            ]
        return {"analysis": analysis, "teacher_report": teacher_report, "pages": pages}

    @app.get("/api/exams/{exam_id}/analysis/pages/{page_id}/content")
    def analysis_page_content(
        exam_id: str,
        page_id: str,
        identity: dict[str, Any] = Depends(current_identity),
    ):
        exam = owner_record("exam", exam_id, identity["user"]["id"])
        data = exam["data"]
        if not data.get("intake_path") or not data.get("asset_root"):
            _http_error(404, "analysis page not found")
        intake = ExamIntake.model_validate_json(
            _safe_path(Path(settings.data_dir), data["intake_path"]).read_bytes()
        )
        page = next((item for item in intake.pages if item.id == page_id), None)
        if page is None:
            _http_error(404, "analysis page not found")
        asset_root = _safe_directory(Path(settings.data_dir), data["asset_root"])
        path = _safe_path(asset_root, page.image_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != page.image_sha256:
            _http_error(404, "analysis page not found")
        return FileResponse(path, media_type="image/png", filename=f"{page.id}.png")

    @app.post("/api/exams/{exam_id}/corrections", status_code=202)
    def create_corrections(
        exam_id: str,
        payload: _Corrections,
        identity: dict[str, Any] = Depends(write_identity),
    ):
        owner_id = identity["user"]["id"]
        exam = owner_record("exam", exam_id, owner_id)
        data = exam["data"]
        if not data.get("analysis_path"):
            _http_error(409, "analysis is not available yet")
        analysis = ExamAnalysis.model_validate(_read_private_json(settings, data["analysis_path"]))
        try:
            from app.exam_analysis import apply_exam_corrections

            normalized = [ExamCorrection.model_validate(item) for item in payload.corrections]
            curriculum = _load_curriculum(settings)
            apply_exam_corrections(
                analysis,
                normalized,
                intake=ExamIntake.model_validate_json(
                    _safe_path(Path(settings.data_dir), data["intake_path"]).read_bytes()
                ),
                curriculum=curriculum,
            )
        except (ValidationError, ValueError, KeyError):
            _http_error(422, "correction does not match the current analysis")
        base = _job_input(store, settings, "correction", exam)
        base["corrections"] = [item.model_dump(mode="json") for item in normalized]
        idempotency = _hash_json({"kind": "correction", "exam": exam_id, "input": base})
        job = store.enqueue_job(owner_id, "correction", exam_id, base, idempotency_key=idempotency)
        return _job_public(job)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, identity: dict[str, Any] = Depends(current_identity)):
        job = store.get_job(job_id, owner_id=identity["user"]["id"])
        if job is None:
            _http_error(404, "job not found")
        return _job_public(job)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, identity: dict[str, Any] = Depends(write_identity)):
        owner_id = identity["user"]["id"]
        previous = store.get_job(job_id, owner_id=owner_id)
        if previous is None:
            _http_error(404, "job not found")
        if previous["status"] != "failed":
            _http_error(409, "only failed jobs can be retried")
        retry_key = _hash_json({"retry_of": previous["id"], "attempts": previous["attempts"]})
        job = store.enqueue_job(
            owner_id,
            previous["kind"],
            previous["resource_id"],
            previous["input_data"],
            idempotency_key=retry_key,
        )
        return _job_public(job)

    @app.get("/api/inventory")
    def inventory(student_id: str, identity: dict[str, Any] = Depends(current_identity)):
        from app.admin_bank import effective_settings

        active = effective_settings(store, settings)
        if not active.bank_dir or not active.semantic_dir:
            _http_error(409, "practice bank is not configured")
        owner_id = identity["user"]["id"]
        student = owner_record("student", student_id, owner_id)
        from app.web_jobs import get_inventory

        try:
            return get_inventory(store, settings, owner_id, student["id"])
        except (OSError, ValueError, ValidationError):
            _http_error(409, "practice inventory is unavailable")

    @app.get("/api/students/{student_id}/practices")
    def student_practices(student_id: str, identity: dict[str, Any] = Depends(current_identity)):
        owner_id = identity["user"]["id"]
        owner_record("student", student_id, owner_id)
        return [
            practice_view(record)
            for record in store.list_records("practice", owner_id, parent_id=student_id)
        ]

    @app.post("/api/practices", status_code=202)
    def create_practice(
        payload: _PracticeCreate, identity: dict[str, Any] = Depends(write_identity)
    ):
        owner_id = identity["user"]["id"]
        student = owner_record("student", payload.student_id, owner_id)
        from app.admin_bank import effective_settings

        active = effective_settings(store, settings)
        if not active.bank_dir or not active.semantic_dir:
            _http_error(409, "practice bank is not configured")
        if len(payload.exam_ids) != len(set(payload.exam_ids)):
            _http_error(422, "exam identifiers must be unique")
        try:
            quotas = [Quota.model_validate(item) for item in payload.quotas]
            PracticeRequest(quotas=quotas, seed=payload.seed)
            curriculum = _load_curriculum(settings)
            index = CurriculumIndex(curriculum)
            index.validate_ids([quota.point_id for quota in quotas])
            learned = set(student["data"]["learned_point_ids"])
            if not learned:
                _http_error(409, "student progress is not set")
            required = index.required([quota.point_id for quota in quotas])
            if not required <= learned:
                _http_error(409, "student has not learned every requested point prerequisite")
        except ValidationError:
            _http_error(422, "practice request is invalid")
        for exam_id in payload.exam_ids:
            owner_record("exam", exam_id, owner_id)
        practice_id = uuid.uuid4().hex
        data = {
            "student_id": payload.student_id,
            "progress_revision": str(student["revision"]),
            "exam_ids": payload.exam_ids,
            "quotas": [quota.model_dump(mode="json") for quota in quotas],
            "seed": payload.seed,
            "layout": payload.layout,
            "pdf_engine": payload.pdf_engine,
            "status": "queued",
            "snapshot": None,
            "files": [],
            "warnings": [],
            "feedback": None,
            "preview": None,
        }
        resource = {
            "id": practice_id,
            "kind": "practice",
            "owner_id": owner_id,
            "parent_id": payload.student_id,
            "revision": 1,
            "data": data,
        }
        job_input = _job_input(store, settings, "practice", resource)
        if str(job_input.get("progress_revision")) != data["progress_revision"]:
            _http_error(409, "学生教学进度已变化，请刷新后重新提交练习")
        idem = _hash_json({"kind": "practice", "resource": practice_id, "input": job_input})
        record = store.create_record(
            "practice", owner_id, data, parent_id=payload.student_id, record_id=practice_id
        )
        try:
            job = store.enqueue_job(
                owner_id, "practice", practice_id, job_input, idempotency_key=idem
            )
        except Exception:
            store.update_record(
                "practice", practice_id, owner_id, {**data, "status": "failed"}, record["revision"]
            )
            raise
        return {"id": record["id"], "job_id": job["id"], "status": job["status"]}

    @app.get("/api/practices/{practice_id}")
    def get_practice(practice_id: str, identity: dict[str, Any] = Depends(current_identity)):
        record = owner_record("practice", practice_id, identity["user"]["id"])
        return practice_view(record)

    @app.get("/api/practices/{practice_id}/files/{name}")
    def practice_file(
        practice_id: str, name: str, identity: dict[str, Any] = Depends(current_identity)
    ):
        if not _SAFE_FILE.fullmatch(name):
            _http_error(404, "file not found")
        record = owner_record("practice", practice_id, identity["user"]["id"])
        student = store.get_record(
            "student", record["data"].get("student_id", ""), record["owner_id"]
        )
        frozen_progress = record["data"].get("progress_revision")
        if (
            student is None
            or type(frozen_progress) not in {str, int}
            or str(frozen_progress) != str(student["revision"])
        ):
            _http_error(409, "学生教学进度已变化或未绑定，请重新生成练习后下载")
        if record["data"].get("status") not in {"completed", "partial"}:
            _http_error(409, "practice files are not available yet")
        item = next(
            (item for item in record["data"].get("files", []) if item.get("name") == name), None
        )
        if item is None:
            _http_error(404, "file not found")
        path = _safe_path(Path(settings.data_dir), item["path"])
        if item.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            _http_error(404, "file not found")
        suffix = path.suffix.lower()
        filename = item.get("filename")
        if (
            not isinstance(filename, str)
            or not _SAFE_FILE.fullmatch(filename)
            or Path(filename).suffix.lower() != suffix
        ):
            filename = name if Path(name).suffix.lower() == suffix else name + suffix
        media_type = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(suffix, "application/octet-stream")
        return FileResponse(
            path, media_type=media_type, filename=filename
        )

    @app.post("/api/practices/{practice_id}/feedback")
    def practice_feedback(
        practice_id: str,
        payload: _Feedback,
        identity: dict[str, Any] = Depends(write_identity),
    ):
        owner_id = identity["user"]["id"]
        practice = owner_record("practice", practice_id, owner_id)
        if practice["data"].get("status") not in {"completed", "partial"}:
            _http_error(409, "feedback requires a completed practice")
        feedbacks = store.list_records("feedback", owner_id, parent_id=practice_id)
        data = {
            **payload.model_dump(mode="json"),
            "practice_id": practice_id,
            "practice_revision": practice["revision"],
        }
        if feedbacks:
            saved = store.update_record(
                "feedback", feedbacks[0]["id"], owner_id, data, feedbacks[0]["revision"]
            )
        else:
            saved = store.create_record("feedback", owner_id, data, parent_id=practice_id)
        return {"id": saved["id"], **saved["data"]}

    @app.get("/api/admin/users")
    def list_users(_identity: dict[str, Any] = Depends(admin_identity)):
        return store.list_users()

    @app.get("/api/billing/me")
    def own_wallet(identity: dict[str, Any] = Depends(current_identity)):
        return {**billing.get_wallet(identity["user"]["id"]), "currency": "CNY"}

    @app.get("/api/admin/billing/defaults")
    def credit_defaults(_identity: dict[str, Any] = Depends(admin_identity)):
        return billing.get_defaults()

    @app.post("/api/admin/billing/defaults")
    def save_credit_defaults(
        payload: _CreditDefaultsSave, identity: dict[str, Any] = Depends(write_admin_identity)
    ):
        return billing.save_defaults(
            payload.new_user_credit, payload.expected_revision, identity["user"]["id"]
        )

    @app.get("/api/admin/deepseek")
    def deepseek_configuration(_identity: dict[str, Any] = Depends(admin_identity)):
        return model_settings.public()

    @app.post("/api/admin/deepseek")
    def save_deepseek(
        payload: _ModelSave, identity: dict[str, Any] = Depends(write_admin_identity)
    ):
        return model_settings.save(
            expected_revision=payload.expected_revision,
            api_key=payload.api_key.get_secret_value() if payload.api_key is not None else None,
            model=payload.model,
            prices=payload.prices,
            actor_id=identity["user"]["id"],
        )

    @app.post("/api/admin/deepseek/check")
    def check_deepseek(_identity: dict[str, Any] = Depends(write_admin_identity)):
        config, _snapshot = model_settings.runtime(timeout=10)
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.get(
                    config.base_url + "/user/balance",
                    headers={"Authorization": f"Bearer {config.api_key}"},
                )
            if response.status_code != 200:
                _http_error(502, "DeepSeek 余额检查失败，请确认密钥、账户和网络")
            value = response.json()
            if type(value.get("is_available")) is not bool or not isinstance(
                value.get("balance_infos"), list
            ):
                raise ValueError
            balances = []
            for row in value["balance_infos"]:
                if row.get("currency") not in {"CNY", "USD"}:
                    raise ValueError
                balance = {"currency": row["currency"]}
                for field in ("total_balance", "granted_balance", "topped_up_balance"):
                    amount = row.get(field)
                    if not isinstance(amount, str) or not re.fullmatch(
                        r"-?\d{1,15}(?:\.\d{1,8})?", amount
                    ):
                        raise ValueError
                    balance[field] = amount
                balances.append(balance)
            return {"ok": True, "is_available": value["is_available"], "balance_infos": balances}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            _http_error(502, "DeepSeek 余额检查失败，请确认密钥、账户和网络")

    @app.get("/api/admin/billing/wallets")
    def all_wallets(_identity: dict[str, Any] = Depends(admin_identity)):
        return {
            "currency": "CNY",
            "users": [
                {
                    **billing.get_wallet(user["id"]),
                    **{k: user[k] for k in ("username", "display_name", "role")},
                }
                for user in store.list_users()
            ],
        }

    @app.post("/api/admin/billing/users/{user_id}/credits")
    def increase_credits(
        user_id: str, payload: _CreditAdd, identity: dict[str, Any] = Depends(write_admin_identity)
    ):
        if not any(user["id"] == user_id for user in store.list_users()):
            _http_error(404, "用户不存在")
        try:
            return billing.top_up(
                user_id, payload.amount, payload.request_id, identity["user"]["id"]
            )
        except ValueError:
            _http_error(422, "额度金额或请求标识无效")

    @app.get("/api/admin/billing/yesterday")
    def yesterday_cost(_identity: dict[str, Any] = Depends(admin_identity)):
        report = billing.daily_summary()
        users = {user["id"]: user for user in store.list_users()}
        for row in report["users"]:
            user = users.get(row["user_id"], {})
            row.update(
                username=user.get("username", "已删除用户"),
                display_name=user.get("display_name", "已删除用户"),
            )
        return report

    @app.get("/api/admin/billing/unresolved")
    def unresolved_calls(_identity: dict[str, Any] = Depends(admin_identity)):
        return {
            "calls": [
                {**row, "can_resolve": can_resolve_attempt(row)}
                for row in billing.list_unresolved()
            ]
        }

    def can_resolve_attempt(attempt: dict) -> bool:
        if attempt["status"] != "reserved":
            return True
        job = store.get_job(attempt["job_id"], attempt["user_id"])
        if not job or job["status"] != "running":
            return True
        expiry = job.get("lease_expires_at")
        return bool(expiry) and datetime.fromisoformat(
            expiry.replace("Z", "+00:00")
        ) <= datetime.now(UTC)

    @app.post("/api/admin/billing/calls/{attempt_id}/resolve")
    def resolve_call(
        attempt_id: str,
        payload: _ResolveCredit,
        identity: dict[str, Any] = Depends(write_admin_identity),
    ):
        try:
            attempt = billing.get_attempt(attempt_id)
            if attempt["status"] == "reserved":
                if not can_resolve_attempt(attempt):
                    _http_error(409, "模型请求仍在处理中，暂不能核实该预占")
                billing.settle_attempt(attempt_id, None, None, "process_interrupted")
            return billing.resolve_attempt(
                attempt_id,
                payload.amount,
                payload.reason,
                payload.request_id,
                identity["user"]["id"],
            )
        except ValueError:
            _http_error(422, "核实金额、原因或请求标识无效")

    @app.post("/api/admin/users", status_code=201)
    def create_user(
        payload: _UserCreate, _identity: dict[str, Any] = Depends(write_admin_identity)
    ):
        try:
            return store.create_user(
                payload.username, payload.password, payload.display_name, "teacher"
            )
        except StoreConflict:
            _http_error(409, "username already exists")

    @app.get("/api/admin/prompts")
    def list_prompts(_identity: dict[str, Any] = Depends(admin_identity)):
        return store.list_prompts()

    @app.get("/api/admin/prompts/{key}/history")
    def prompt_history(key: str, _identity: dict[str, Any] = Depends(admin_identity)):
        history = store.prompt_history(key)
        if not history:
            _http_error(404, "prompt not found")
        return history

    @app.post("/api/admin/prompts/{key}")
    def save_prompt(
        key: str,
        payload: _PromptSave,
        identity: dict[str, Any] = Depends(write_admin_identity),
    ):
        try:
            return store.save_prompt(
                key, payload.body, identity["user"]["id"], payload.expected_revision
            )
        except StoreNotFound:
            _http_error(404, "prompt not found")

    @app.post("/api/admin/prompts/{key}/activate")
    def activate_prompt(
        key: str,
        payload: _PromptActivate,
        identity: dict[str, Any] = Depends(write_admin_identity),
    ):
        try:
            return store.activate_prompt(
                key, payload.revision, identity["user"]["id"], payload.expected_revision
            )
        except StoreNotFound:
            _http_error(404, "prompt or revision not found")

    from app.admin_bank_api import install_bank_routes

    install_bank_routes(app, store, settings, admin_identity, write_admin_identity)

    frontend = Path(settings.frontend_dist)
    if frontend.is_dir():
        from fastapi.staticfiles import StaticFiles

        static_dir = frontend / "static"
        if static_dir.is_dir():
            app.mount("/static", StaticFiles(directory=static_dir), name="frontend-static")
        asset_dir = frontend / "assets"
        if asset_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=asset_dir), name="frontend-assets")

    @app.get("/", response_class=HTMLResponse)
    def frontend_index():
        index = frontend / "index.html"
        if not index.is_file():
            _http_error(404, "frontend is not built")
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.get("/{path:path}", response_class=HTMLResponse)
    def frontend_fallback(path: str):
        private_prefixes = (
            "api",
            "assets",
            "jobs",
            "private",
            "work",
            "uploads",
            "sources",
            "data",
        )
        first = path.split("/", 1)[0]
        if first in private_prefixes:
            _http_error(404, "not found")
        index = frontend / "index.html"
        if not index.is_file():
            _http_error(404, "not found")
        return HTMLResponse(index.read_text(encoding="utf-8"))

    return app
