"""Standalone, bounded HTTP service for heavyweight document and browser tools."""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VERSION = "tools-v1"
CAPABILITIES = ["word-pdf", "tex-pdf", "pandoc", "screenshot"]
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_LOG_CHARS = 16 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
OPERATION_TIMEOUT_SECONDS = 110
SCREENSHOT_TIMEOUT_MS = 40_000
BUSY_TIMEOUT_SECONDS = 2
_RUN_LOCK = threading.BoundedSemaphore(1)


class RequestError(ValueError):
    """A safe, client-visible request validation error."""


def _resolve_under_root(raw: Any, root: Path, *, must_exist: bool, kind: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RequestError("invalid path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise RequestError("invalid path")
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        raise RequestError("path outside work root or unavailable") from None
    if must_exist:
        if kind == "file" and not resolved.is_file():
            raise RequestError("source must be a file")
        if kind == "directory" and not resolved.is_dir():
            raise RequestError("path must be a directory")
    else:
        existing = resolved
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        try:
            existing.resolve(strict=True).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            raise RequestError("path outside work root or unavailable") from None
    return resolved


def _executable(env_name: str, default_name: str) -> str:
    configured = os.environ.get(env_name)
    if configured:
        candidate = Path(configured)
        if candidate.is_absolute():
            try:
                resolved = candidate.resolve(strict=True)
                if resolved.is_file() and os.access(resolved, os.X_OK):
                    # Some tools (notably xelatex/xetex) select behavior from argv[0].
                    # Validate the resolved target but invoke via the configured alias.
                    return str(candidate)
            except (OSError, RuntimeError):
                pass
        raise RequestError("tool unavailable")
    found = shutil.which(default_name)
    if not found:
        raise RequestError("tool unavailable")
    return found


def _run(argv: list[str], *, cwd: Path, timeout: int = OPERATION_TIMEOUT_SECONDS) -> dict:
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            stdout_file.seek(0, os.SEEK_END)
            stderr_file.seek(0, os.SEEK_END)
            stdout_file.seek(max(0, stdout_file.tell() - MAX_LOG_CHARS))
            stderr_file.seek(max(0, stderr_file.tell() - MAX_LOG_CHARS))
            stdout = stdout_file.read().decode("utf-8", errors="replace")
            stderr = stderr_file.read().decode("utf-8", errors="replace")
            return {
                "version": VERSION,
                "returncode": 124,
                "stdout": stdout,
                "stderr": stderr or "tool timed out",
            }
        stdout_file.seek(0, os.SEEK_END)
        stdout_size = stdout_file.tell()
        stderr_file.seek(0, os.SEEK_END)
        stderr_size = stderr_file.tell()
        truncated = stdout_size > MAX_LOG_CHARS or stderr_size > MAX_LOG_CHARS
        stdout_file.seek(max(0, stdout_size - MAX_LOG_CHARS))
        stderr_file.seek(max(0, stderr_size - MAX_LOG_CHARS))
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
    return {
        "version": VERSION,
        "returncode": 125 if truncated else completed.returncode,
        "stdout": stdout,
        "stderr": "tool output exceeded 16 KiB; result rejected" if truncated else stderr,
    }


def _ensure_output_dir(raw: Any, root: Path) -> Path:
    path = _resolve_under_root(raw, root, must_exist=False, kind="directory")
    path.mkdir(parents=True, exist_ok=True)
    # Re-resolve after mkdir to catch a concurrently replaced symlink.
    try:
        path = path.resolve(strict=True)
        path.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        raise RequestError("path outside work root or unavailable") from None
    if not path.is_dir():
        raise RequestError("path must be a directory")
    return path


def _word_pdf(body: dict, root: Path) -> dict:
    source = _resolve_under_root(body.get("source"), root, must_exist=True, kind="file")
    output = _ensure_output_dir(body.get("output_dir"), root)
    profile = _ensure_output_dir(body.get("profile_dir"), root)
    executable = _executable("TOOL_SOFFICE", "soffice")
    profile_uri = profile.as_uri()
    return _run(
        [executable, "--headless", f"-env:UserInstallation={profile_uri}", "--convert-to", "pdf", "--outdir", str(output), str(source)],
        cwd=root,
    )


def _tex_pdf(body: dict, root: Path) -> dict:
    source = _resolve_under_root(body.get("source"), root, must_exist=True, kind="file")
    output = _ensure_output_dir(body.get("output_dir"), root)
    executable = _executable("TOOL_XELATEX", "xelatex")
    return _run(
        [executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", f"-output-directory={output}", f"./{source.name}"],
        cwd=source.parent,
    )


def _pandoc(body: dict, root: Path) -> dict:
    source = _resolve_under_root(body.get("source"), root, must_exist=True, kind="file")
    target = _resolve_under_root(body.get("target"), root, must_exist=False, kind="file")
    target.parent.mkdir(parents=True, exist_ok=True)
    target = target.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        raise RequestError("path outside work root or unavailable") from None
    executable = _executable("TOOL_PANDOC", "pandoc")
    return _run(
        [executable, "--from=markdown+tex_math_dollars-raw_html", "--to=docx", "--output", str(target), str(source)],
        cwd=source.parent,
    )


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise RuntimeError("invalid screenshot output")
    return struct.unpack(">II", data[16:24])


def _screenshot(body: dict) -> dict:
    html = body.get("html")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise RequestError("invalid html")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RequestError("browser unavailable") from None
    browser_executable = os.environ.get("TOOL_CHROMIUM")
    if browser_executable:
        browser_path = Path(browser_executable)
        if not browser_path.is_absolute() or not browser_path.is_file() or not os.access(browser_path, os.X_OK):
            raise RequestError("browser unavailable")
        browser_executable = str(browser_path.resolve())
    else:
        browser_executable = next(
            (path for name in ("chromium", "chromium-browser", "google-chrome") if (path := shutil.which(name))),
            None,
        )
    if not browser_executable:
        raise RequestError("browser unavailable")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=browser_executable,
        )
        try:
            context = browser.new_context(
                viewport={"width": 1100, "height": 1200},
                device_scale_factor=1,
                java_script_enabled=False,
                service_workers="block",
            )
            blocked: list[str] = []

            def guard(route: Any) -> None:
                scheme = urlparse(route.request.url).scheme.lower()
                if scheme in {"data", "about"}:
                    route.continue_()
                else:
                    blocked.append(scheme or "unknown")
                    route.abort()

            context.route("**/*", guard)
            page = context.new_page()
            page.set_default_timeout(SCREENSHOT_TIMEOUT_MS)
            page.set_content(html, wait_until="load", timeout=SCREENSHOT_TIMEOUT_MS)
            page.evaluate("() => document.fonts.ready.then(() => true)")
            image_state = page.locator("img").evaluate_all(
                "images => images.map(img => ({ok: img.complete && img.naturalWidth > 0}))"
            )
            if blocked:
                raise RequestError("external browser requests are forbidden")
            if any(not item["ok"] for item in image_state):
                raise RequestError("one or more images failed to load")
            size = page.evaluate("""() => ({
                width: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
                height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
                overflow: [...document.querySelectorAll('main,section,p,table')].some(el =>
                    el.scrollWidth > el.clientWidth && getComputedStyle(el).overflowX !== 'visible')
            })""")
            if size["width"] <= 0 or size["height"] <= 0 or size["overflow"]:
                raise RequestError("rendered content has invalid or clipped layout")
            if size["width"] != 1100:
                page.set_viewport_size({"width": size["width"], "height": 1200})
            png = page.screenshot(full_page=True, type="png", animations="disabled", timeout=SCREENSHOT_TIMEOUT_MS)
            width, height = _png_size(png)
            if width < size["width"] or height < size["height"]:
                raise RequestError("screenshot does not cover rendered content")
            if len(png) > MAX_RESPONSE_BYTES:
                raise RequestError("screenshot exceeds response limit")
            return {"version": VERSION, "png_base64": base64.b64encode(png).decode("ascii"), "width": width, "height": height}
        finally:
            browser.close()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], root: Path):
        self.root = root.resolve()
        self.started_at = time.monotonic()
        super().__init__(address, _Handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, _format: str, *_args: Any) -> None:
        # Request data and command output never enter access logs.
        return

    def _send(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"version": VERSION, "capabilities": CAPABILITIES})

    def do_POST(self) -> None:
        operations = {
            "/v1/word-pdf": _word_pdf,
            "/v1/tex-pdf": _tex_pdf,
            "/v1/pandoc": _pandoc,
            "/v1/screenshot": _screenshot,
        }
        operation = operations.get(self.path)
        if operation is None:
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._send(413, {"error": "invalid or oversized request"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise RequestError("invalid request")
        except (json.JSONDecodeError, UnicodeDecodeError, RequestError):
            self._send(400, {"error": "invalid request"})
            return
        if not _RUN_LOCK.acquire(timeout=BUSY_TIMEOUT_SECONDS):
            self._send(503, {"error": "worker busy"})
            return
        try:
            result = operation(body, self.server.root) if operation is not _screenshot else operation(body)
            self._send(200, result)
        except RequestError as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "tool execution failed"})
        finally:
            _RUN_LOCK.release()


def create_server(host: str = "0.0.0.0", port: int = 8001, root: str | Path | None = None) -> ThreadingHTTPServer:
    """Create the worker server; root defaults to ``TOOL_WORK_ROOT`` or ``/app/work``."""
    work_root = Path(root or os.environ.get("TOOL_WORK_ROOT", "/app/work")).resolve()
    if not work_root.is_dir():
        raise ValueError("work root must exist and be a directory")
    (work_root / "tmp").mkdir(exist_ok=True)
    return _Server((host, port), work_root)


def main() -> None:
    server = create_server()
    server.serve_forever()


if __name__ == "__main__":
    main()
