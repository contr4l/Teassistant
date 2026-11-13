"""Administrator-only bank routes; upload inputs never choose server paths."""

from __future__ import annotations

import hashlib
import io
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app import admin_bank as bank
from app.web_store import StoreConflict


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=1, strict=True)


class _Adjustment(_Input):
    enabled: bool = Field(strict=True)
    point_ids: list[str] = Field(min_length=1, max_length=100)
    prerequisite_ids: list[str] = Field(max_length=100)
    difficulty_score: int = Field(ge=0, le=100, strict=True)
    reason: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2000)


class _Restore(_Input):
    version: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)


def _validate_docx(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if (len(entries) > 4096 or len(set(names)) != len(names)
                    or sum(e.file_size for e in entries) > 128 * 1024 * 1024
                    or "word/document.xml" not in names or "[Content_Types].xml" not in names):
                raise ValueError
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                        or entry.flag_bits & 1 or "vbaproject" in entry.filename.casefold()
                        or ((entry.external_attr >> 16) & 0o170000) == 0o120000):
                    raise ValueError
    except (ValueError, OSError, zipfile.BadZipFile):
        raise bank.BankError("上传资料须为有效、无宏的DOCX，且解压体积不超过128MiB") from None


def install_bank_routes(app, store, settings, admin_identity, write_admin_identity):
    @app.exception_handler(bank.BankError)
    async def bank_error(_request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/admin/bank")
    def summary(identity=Depends(admin_identity)):
        return bank.summary(store, settings, requester_id=identity["user"]["id"])

    @app.get("/api/admin/bank/questions")
    def questions(
        q: Annotated[str, Query(max_length=200)] = "",
        status: Literal["all", "eligible", "blocked", "disabled"] = "all",
        point_id: Annotated[str, Query(max_length=100)] = "",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        identity=Depends(admin_identity),
    ):
        return bank.list_questions(store, settings, q=q, status=status, point_id=point_id,
                                   offset=offset, limit=limit)

    @app.get("/api/admin/bank/questions/{question_id}")
    def detail(question_id: str, identity=Depends(admin_identity)):
        try:
            return bank.detail(store, settings, question_id)
        except KeyError:
            raise HTTPException(404, "题目不存在") from None

    @app.patch("/api/admin/bank/questions/{question_id}")
    def adjust(question_id: str, payload: _Adjustment, identity=Depends(write_admin_identity)):
        try:
            return bank.save_adjustment(store, settings, question_id,
                                        payload.model_dump(), identity["user"]["id"])
        except KeyError:
            raise HTTPException(404, "题目不存在") from None
        except ValueError as exc:
            if isinstance(exc, bank.BankError):
                raise
            raise HTTPException(422, "考点或题目调整内容无效") from None

    @app.post("/api/admin/bank/check")
    def check(payload: _Input, identity=Depends(write_admin_identity)):
        if bank.state(store)["revision"] != payload.expected_revision:
            raise StoreConflict("题库已更新，请刷新后重试")
        return bank.summary(store, settings, requester_id=identity["user"]["id"])

    @app.post("/api/admin/bank/restore")
    def restore(payload: _Restore, identity=Depends(write_admin_identity)):
        return bank.restore(store, settings, payload.version, payload.expected_revision,
                            identity["user"]["id"], payload.reason)

    @app.post("/api/admin/bank/imports", status_code=202)
    async def upload(
        files: Annotated[list[UploadFile], File()],
        expected_revision: Annotated[int, Form(ge=1)],
        reason: Annotated[str, Form(min_length=1, max_length=500)],
        identity=Depends(write_admin_identity),
    ):
        reason = reason.strip()
        if not reason or not 2 <= len(files) <= 40:
            raise bank.BankError("请填写更新原因，上传2～40份原卷和解析DOCX")
        current = bank.state(store)
        if current["revision"] != expected_revision:
            raise StoreConflict("题库已更新，请刷新后重试")
        identifier = uuid.uuid4().hex
        directory = settings.data_dir / "bank-uploads" / identifier
        directory.mkdir(parents=True, exist_ok=False)
        names, rows, total = set(), [], 0
        try:
            for number, file in enumerate(files):
                filename = file.filename or ""
                if (Path(filename).name != filename or "\\" in filename
                        or any(ord(c) < 32 for c in filename) or len(filename) > 240
                        or not filename.lower().endswith(".docx") or filename.casefold() in names):
                    raise bank.BankError("文件名无效、重复或格式非DOCX")
                names.add(filename.casefold())
                raw = await file.read(30 * 1024 * 1024 + 1)
                total += len(raw)
                if len(raw) > 30 * 1024 * 1024 or total > 200 * 1024 * 1024:
                    raise HTTPException(413, "每份文件不超过30MiB，一批不超过200MiB")
                _validate_docx(raw)
                path = directory / filename
                path.write_bytes(raw)
                path.chmod(0o600)
                rows.append({"filename": filename, "path": path.relative_to(settings.data_dir).as_posix(),
                             "sha256": hashlib.sha256(raw).hexdigest()})
            # Detect version changes during a potentially long multipart upload.
            if bank.state(store)["revision"] != expected_revision:
                raise StoreConflict("上传期间题库已更新，请刷新后重试")
            owner = identity["user"]["id"]
            data = {"files": rows, "reason": reason, "status": "queued"}
            resource = store.create_record("bank_import", owner, data, record_id=identifier)
            frozen = {
                **data, "bank_revision": expected_revision, "resource_revision": 1,
                "curriculum_sha256": hashlib.sha256(settings.curriculum_path.read_bytes()).hexdigest(),
            }
            job = store.enqueue_job(owner, "bank_import", resource["id"], frozen,
                                    idempotency_key="bank-import-" + identifier)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return {k: job.get(k) for k in (
            "id", "kind", "resource_id", "status", "error", "result", "attempts", "created_at"
        )}
