#!/usr/bin/env python3
"""Run an isolated, no-credentials PostgreSQL/API/worker smoke against an image."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}$")
_SAFE_FAILURE_HINTS = frozenset({
    "container API did not become ready",
    "smoke API port is not loopback-bound",
    "synthetic admin login failed",
    "synthetic student creation failed",
    "synthetic exam creation failed",
    "synthetic exam asset upload failed",
    "synthetic analysis did not enter the persistent queue",
    "queued job could not be read",
    "no-credential analysis job did not fail closed",
    "synthetic feedback persistence failed",
    "synthetic feedback was not read back",
})
_COMMAND_ERROR_MARKERS = (
    "EOFError", "ModuleNotFoundError", "ImportError", "PermissionError",
    "FileNotFoundError", "AssertionError", "ValueError", "KeyError",
    "OperationalError", "IntegrityError", "ProgrammingError",
)
_CLIENT_ERROR_TYPES = frozenset({
    "KeyError", "OSError", "RuntimeError", "StoreConflict", "StoreError",
    "StoreNotFound", "TimeoutError", "TypeError", "URLError", "ValueError",
    "ProtocolError", "OtherError",
})


class SmokeCommandError(RuntimeError):
    def __init__(self, exit_code: int, markers: tuple[str, ...] = ()):
        self.exit_code = exit_code
        self.markers = tuple(marker for marker in _COMMAND_ERROR_MARKERS if marker in markers)
        super().__init__(f"container smoke command failed (exit {exit_code})")


class SmokeClientError(RuntimeError):
    """Safe, fixed diagnostics returned by the in-container HTTP client."""

    def __init__(self, error_type: str, hint: str | None = None):
        self.error_type = error_type if error_type in _CLIENT_ERROR_TYPES else "OtherError"
        self.hint = hint if hint in _SAFE_FAILURE_HINTS else None
        super().__init__(self.error_type)


class SmokeStageError(RuntimeError):
    """Controlled diagnostic without subprocess output, credentials or input paths."""

    def __init__(self, stage: str, error: Exception):
        self.summary = f"stage={stage}; error={type(error).__name__}"
        if isinstance(error, SmokeCommandError):
            self.summary += f"; exit={error.exit_code}"
            if error.markers:
                self.summary += f"; markers={','.join(error.markers)}"
        elif isinstance(error, SmokeClientError):
            self.summary = f"stage={stage}; error={error.error_type}"
            if error.hint:
                self.summary += f"; hint={error.hint}"
        elif type(error) is RuntimeError and str(error) in _SAFE_FAILURE_HINTS:
            self.summary += f"; hint={error}"
        super().__init__(self.summary)


def _at_stage(stage, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except SmokeStageError:
        raise
    except Exception as exc:
        raise SmokeStageError(stage, exc) from exc


def _validate_image(image: str) -> str:
    if not isinstance(image, str) or not _IMAGE_REF.fullmatch(image):
        raise ValueError("invalid Docker image reference")
    return image


def _compose_text(image: str, share: Path, db_password: str) -> str:
    image_yaml = json.dumps(image)
    # Keep the argument for callers; container-owned outputs stay in named volumes.
    password_yaml = json.dumps(db_password)
    app_env = """      TEASSISTANT_DATABASE_URL: postgresql+psycopg://smoke:{db}@postgres:5432/smoke
      TEASSISTANT_DATA_DIR: /app/work/private
      TEASSISTANT_CURRICULUM: /app/data/curricula/junior-math-2022-draft-v2.json
      TEASSISTANT_FRONTEND_DIST: /app/frontend/dist
      TEASSISTANT_MODEL_PROVIDER: deepseek
      DEEPSEEK_API_KEY: ""
      TEASSISTANT_ALLOW_CODEX: "0"
      TEASSISTANT_SECURE_COOKIES: "0"
""".replace("{db}", db_password)
    return f"""services:
  postgres:
    image: postgres:17-bookworm
    environment:
      POSTGRES_DB: smoke
      POSTGRES_USER: smoke
      POSTGRES_PASSWORD: {password_yaml}
    healthcheck:
      test: [CMD-SHELL, pg_isready -U smoke -d smoke]
      interval: 2s
      timeout: 2s
      retries: 30
    volumes:
      - smoke_db:/var/lib/postgresql/data
  init:
    image: {image_yaml}
    healthcheck:
      disable: true
    environment:
{app_env}
    volumes:
      - smoke_data:/app/work
    depends_on:
      postgres:
        condition: service_healthy
    command: [/app/.venv/bin/python, /app/scripts/manage_web.py, init]
  api:
    image: {image_yaml}
    environment:
{app_env}
    volumes:
      - smoke_data:/app/work
    depends_on:
      init:
        condition: service_completed_successfully
    command: [/app/.venv/bin/uvicorn, app.web_api:create_app, --factory, --host, 0.0.0.0, --port, "8000"]
  worker:
    image: {image_yaml}
    healthcheck:
      test: [CMD, /app/.venv/bin/python, /app/scripts/container_healthcheck.py, --role, worker]
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    environment:
{app_env}
    volumes:
      - smoke_data:/app/work
    depends_on:
      init:
        condition: service_completed_successfully
    command: [/app/.venv/bin/python, /app/scripts/run_web_worker.py]
volumes:
  smoke_db:
  smoke_data:
networks:
  default:
    internal: true
"""


def _run(command: list[str], *, timeout: int, input_text: str | None = None) -> str:
    result = subprocess.run(
        command,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        tail = (result.stdout or "")[-65536:]
        markers = tuple(marker for marker in _COMMAND_ERROR_MARKERS
                        if re.search(r"(?:^|[\s.])" + marker + r"(?=:|\s*$)", tail, re.MULTILINE))
        raise SmokeCommandError(result.returncode, markers)
    return result.stdout


def _json_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    csrf: str | None = None,
    timeout: float = 10,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        # Avoid copying response bodies that may contain source or user data to
        # command logs and smoke reports.
        return exc.code, None


def _multipart_upload(
    opener: urllib.request.OpenerDirector,
    url: str,
    role: str,
    png: bytes,
    csrf: str,
) -> int:
    boundary = "----TeassistantSmoke" + secrets.token_hex(12)
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="role"\r\n\r\n{role}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + png
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-CSRF-Token": csrf,
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _png_1x1() -> bytes:
    # A valid, synthetic 1x1 PNG; it contains no exam or student material.
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def _wait_api(opener: urllib.request.OpenerDirector, base_url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(base_url + "/", timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("container API did not become ready")


def _seed_completed_synthetic_practice(owner_id: str, student_id: str, revision: int) -> str:
    from app.web_settings import WebSettings
    from app.web_store import Store

    settings = WebSettings.from_env()
    store = Store(settings.database_url)
    try:
        row = store.create_record(
            "practice",
            owner_id,
            {
                "student_id": student_id,
                "progress_revision": str(revision),
                "status": "completed",
                "snapshot": {"is_synthetic": True},
                "files": [],
                "warnings": [],
                "feedback": None,
                "preview": {"items": []},
            },
            parent_id=student_id,
        )
        return row["id"]
    finally:
        store.close()


def _api_smoke_client(timeout_seconds: int, username: str, password: str) -> dict[str, Any]:
    """Run the synthetic HTTP workflow from inside the API container."""
    base_url = "http://127.0.0.1:8000"
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    _wait_api(opener, base_url, timeout_seconds)

    status, login = _json_request(
        opener,
        base_url + "/api/auth/login",
        "POST",
        {"username": username, "password": password},
    )
    if status != 200 or not isinstance(login, dict) or not isinstance(login.get("csrf_token"), str):
        raise RuntimeError("synthetic admin login failed")
    owner_id = login["user"]["id"]
    csrf = login["csrf_token"]

    status, student = _json_request(
        opener,
        base_url + "/api/students",
        "POST",
        {"name": "合成冒烟学生", "school": "", "grade": 8, "learned_point_ids": []},
        csrf=csrf,
    )
    if status != 201 or not isinstance(student, dict):
        raise RuntimeError("synthetic student creation failed")

    status, exam = _json_request(
        opener,
        base_url + "/api/exams",
        "POST",
        {"student_id": student["id"], "title": "合成容器队列冒烟", "question_count": 1},
        csrf=csrf,
    )
    if status != 201 or not isinstance(exam, dict):
        raise RuntimeError("synthetic exam creation failed")
    png = _png_1x1()
    for role in ("paper", "response"):
        uploaded = _multipart_upload(
            opener, f"{base_url}/api/exams/{exam['id']}/assets", role, png, csrf
        )
        if uploaded != 201:
            raise RuntimeError("synthetic exam asset upload failed")

    status, job = _json_request(
        opener,
        f"{base_url}/api/exams/{exam['id']}/analyze",
        "POST",
        {"provider": "deepseek"},
        csrf=csrf,
    )
    if status != 202 or not isinstance(job, dict):
        raise RuntimeError("synthetic analysis did not enter the persistent queue")
    job_status = job["status"]
    deadline = time.monotonic() + timeout_seconds
    while job_status in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(1)
        status, job = _json_request(opener, f"{base_url}/api/jobs/{job['id']}", "GET", timeout=8)
        if status != 200 or not isinstance(job, dict):
            raise RuntimeError("queued job could not be read")
        job_status = job["status"]
    if job_status != "failed":
        # With no model key and an internal-only network, a failed configured
        # analysis is expected; a synthetic success would be misleading.
        raise RuntimeError("no-credential analysis job did not fail closed")

    practice_id = _seed_completed_synthetic_practice(
        owner_id, student["id"], student["progress_revision"]
    )
    status, feedback = _json_request(
        opener,
        f"{base_url}/api/practices/{practice_id}/feedback",
        "POST",
        {
            "expectation": "partial",
            "quality": 3,
            "saved_minutes": 0,
            "reasons": ["container-smoke"],
            "comment": "纯合成容器反馈冒烟",
        },
        csrf=csrf,
    )
    if status != 200 or not isinstance(feedback, dict):
        raise RuntimeError("synthetic feedback persistence failed")
    status, practice = _json_request(opener, f"{base_url}/api/practices/{practice_id}", "GET")
    if status != 200 or not practice.get("feedback"):
        raise RuntimeError("synthetic feedback was not read back")

    return {
        "login": "passed",
        "student_create": "passed",
        "uploaded_synthetic_assets": 2,
        "queued_job_worker_terminal_status": job_status,
        "feedback_write_read": "passed",
        "model_call": "not attempted; no credential and internal-only network",
    }


_API_SMOKE_CLIENT_SCRIPT = """import json,sys
sys.path.insert(0, "/app/scripts")
import container_smoke
payload=json.load(sys.stdin)
try:
 result=container_smoke._api_smoke_client(
  payload["timeout_seconds"],payload["username"],payload["password"])
 response={"ok":True,"result":result}
except Exception as exc:
 error_type=type(exc).__name__
 if error_type not in container_smoke._CLIENT_ERROR_TYPES:
  error_type="OtherError"
 hint=str(exc) if type(exc) is RuntimeError and str(exc) in container_smoke._SAFE_FAILURE_HINTS else None
 response={"ok":False,"error_type":error_type,"hint":hint}
print(json.dumps(response,ensure_ascii=False,separators=(",",":")))
"""


def _api_smoke(compose: list[str], timeout: int) -> dict[str, Any]:
    _at_stage(
        "private image content check", _run,
        compose
        + [
            "exec",
            "-T",
            "api",
            "/app/.venv/bin/python",
            "-c",
            "from pathlib import Path; "
            "bad = ['/app/.env', '/app/.env.deploy', '/app/.git', '/app/backend/work', "
            "'/app/runtime', '/app/evals/private', '/app/assets']; "
            "assert not any(Path(p).exists() for p in bad), 'private build content present'",
        ],
        timeout=20,
    )

    _at_stage(
        "API healthcheck", _run,
        compose
        + [
            "exec",
            "-T",
            "api",
            "/app/.venv/bin/python",
            "/app/scripts/container_healthcheck.py",
            "--role",
            "api",
        ],
        timeout=20,
    )
    _at_stage(
        "worker healthcheck", _run,
        compose
        + [
            "exec",
            "-T",
            "worker",
            "/app/.venv/bin/python",
            "/app/scripts/container_healthcheck.py",
            "--role",
            "worker",
        ],
        timeout=20,
    )

    username = "smoke-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    _at_stage(
        "synthetic administrator creation", _run,
        compose
        + [
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "api",
            "/app/.venv/bin/python",
            "/app/scripts/manage_web.py",
            "create-admin",
            "--username",
            username,
            "--display-name",
            "合成容器冒烟管理员",
        ],
        timeout=30,
        input_text=password + "\n" + password + "\n",
    )

    output = _at_stage(
        "in-container HTTP workflow", _run,
        compose
        + [
            "exec",
            "-T",
            "api",
            "/app/.venv/bin/python",
            "-c",
            _API_SMOKE_CLIENT_SCRIPT,
        ],
        timeout=timeout + 30,
        input_text=json.dumps(
            {"timeout_seconds": timeout, "username": username, "password": password}
        ),
    )
    try:
        response = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        raise SmokeStageError(
            "in-container HTTP workflow", SmokeClientError("ProtocolError")
        ) from None
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise SmokeStageError(
            "in-container HTTP workflow", SmokeClientError("ProtocolError")
        )
    if response["ok"] is False:
        if set(response) != {"ok", "error_type", "hint"}:
            raise SmokeStageError(
                "in-container HTTP workflow", SmokeClientError("ProtocolError")
            )
        error_type = response["error_type"]
        hint = response["hint"]
        if (
            not isinstance(error_type, str)
            or error_type not in _CLIENT_ERROR_TYPES
            or (hint is not None and (not isinstance(hint, str) or hint not in _SAFE_FAILURE_HINTS))
        ):
            raise SmokeStageError(
                "in-container HTTP workflow", SmokeClientError("ProtocolError")
            )
        raise SmokeStageError(
            "in-container HTTP workflow", SmokeClientError(error_type, hint)
        )

    expected_fields = {
        "login",
        "student_create",
        "uploaded_synthetic_assets",
        "queued_job_worker_terminal_status",
        "feedback_write_read",
        "model_call",
    }
    result = response.get("result")
    if set(response) != {"ok", "result"} or not isinstance(result, dict) or set(result) != expected_fields:
        raise SmokeStageError(
            "in-container HTTP workflow", SmokeClientError("ProtocolError")
        )
    if (
        result["login"] != "passed"
        or result["student_create"] != "passed"
        or type(result["uploaded_synthetic_assets"]) is not int
        or result["uploaded_synthetic_assets"] != 2
        or result["queued_job_worker_terminal_status"] != "failed"
        or result["feedback_write_read"] != "passed"
        or result["model_call"] != "not attempted; no credential and internal-only network"
    ):
        raise SmokeStageError(
            "in-container HTTP workflow", SmokeClientError("ProtocolError")
        )
    return {
        "api_ready": True,
        "private_build_paths_absent": True,
        "api_healthcheck": "passed",
        "worker_healthcheck": "passed",
        **result,
    }


def run_smoke(
    image: str,
    *,
    docker_executable: str = "docker",
    timeout_seconds: int = 240,
    report_path: Path | None = None,
) -> dict[str, Any]:
    image = _validate_image(image)
    if report_path is not None and (report_path.exists() or report_path.is_symlink()):
        raise ValueError("smoke report path must be new")
    docker = (
        shutil.which(docker_executable) if os.sep not in docker_executable else docker_executable
    )
    if not docker:
        raise ValueError("Docker executable was not found")
    platform = _at_stage(
        "image inspection", _run,
        [docker, "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image],
        timeout=30,
    ).strip()
    project = "ta-smoke-" + secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix="teassistant-container-smoke-") as temp_text:
        temp = Path(temp_text)
        share = temp / "share"
        db_password = secrets.token_urlsafe(24)
        compose_file = temp / "compose.yaml"
        compose_file.write_text(_compose_text(image, share, db_password), encoding="utf-8")
        compose_file.chmod(0o600)
        compose = [docker, "compose", "--project-name", project, "--file", str(compose_file)]
        result: dict[str, Any] = {
            "schema_version": "container-smoke-v1",
            "image": image,
            "platform": platform,
            "model_credentials": "none",
        }
        try:
            _at_stage("database initialization", _run,
                      compose + ["run", "--rm", "-T", "init"], timeout=60)
            _at_stage("service startup", _run,
                      compose + ["up", "--detach", "--wait", "postgres", "api", "worker"],
                      timeout=120)
            result["web_workflow"] = _at_stage("web workflow", _api_smoke, compose, timeout_seconds)
            chromium = _at_stage(
                "Chromium render", _run,
                compose + [
                    "run", "--rm", "-T", "--no-deps", "api", "/app/.venv/bin/python",
                    "/app/scripts/container_smoke_fixture.py", "chromium",
                    "--output", "/app/work/container-smoke/chromium-render",
                ],
                timeout=90,
            )
            word = _at_stage(
                "Word export", _run,
                compose
                + [
                    "run",
                    "--rm",
                    "-T",
                    "--no-deps",
                    "api",
                    "/app/.venv/bin/python",
                    "/app/scripts/container_smoke_fixture.py",
                    "word",
                    "--output",
                    "/app/work/container-smoke/word-export",
                ],
                timeout=240,
            )
            latex = _at_stage(
                "LaTeX export", _run,
                compose
                + [
                    "run",
                    "--rm",
                    "-T",
                    "--no-deps",
                    "api",
                    "/app/.venv/bin/python",
                    "/app/scripts/container_smoke_fixture.py",
                    "latex",
                    "--output",
                    "/app/work/container-smoke/latex-export",
                ],
                timeout=240,
            )
            result["synthetic_word_export"] = json.loads(word.strip().splitlines()[-1])
            result["synthetic_chromium_render"] = json.loads(chromium.strip().splitlines()[-1])
            result["synthetic_latex_export"] = json.loads(latex.strip().splitlines()[-1])
        finally:
            already_failing = sys.exc_info()[0] is not None
            try:
                _at_stage("cleanup", _run,
                          compose + ["down", "--volumes", "--remove-orphans"], timeout=45)
            except SmokeStageError as cleanup_error:
                if not already_failing:
                    raise
                print(f"container smoke cleanup failed: {cleanup_error.summary}", file=sys.stderr)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_name(f".{report_path.name}.{secrets.token_hex(4)}.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, report_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="already-built local Docker image")
    parser.add_argument("--docker-executable", default="docker")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout_seconds <= 900:
        parser.error("--timeout-seconds must be between 30 and 900")
    try:
        report = run_smoke(
            args.image,
            docker_executable=args.docker_executable,
            timeout_seconds=args.timeout_seconds,
            report_path=args.report,
        )
    except SmokeStageError as exc:
        print(f"container smoke failed: {exc.summary}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error title=Container smoke failure::{exc.summary}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"container smoke failed: {type(exc).__name__}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error title=Container smoke failure::stage=host lifecycle; "
                  f"error={type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
