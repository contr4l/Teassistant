from __future__ import annotations

import base64
import http.client
import json
import struct
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import worker


@pytest.fixture
def http_worker(tmp_path: Path):
    root = tmp_path / "work"
    root.mkdir()
    server = worker.create_server("127.0.0.1", 0, root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, root
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(base: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="GET" if data is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health_contract_and_unknown_operations_over_http(http_worker):
    base, _root = http_worker
    status, health = _request(base, "/health")
    assert status == 200
    assert health == {"version": "tools-v1", "capabilities": worker.CAPABILITIES}
    assert _request(base, "/run-anything", {}) == (404, {"error": "not found"})


def test_paths_and_operations_fail_closed_over_http(http_worker, tmp_path: Path, monkeypatch):
    base, root = http_worker
    outside = tmp_path / "outside.tex"
    outside.write_text("\\documentclass{article}")
    (root / "escape.tex").symlink_to(outside)
    monkeypatch.setenv("TOOL_XELATEX", "/nonexistent/xelatex")
    status, result = _request(base, "/v1/tex-pdf", {"source": str(outside), "output_dir": str(root / "out")})
    assert status == 400 and "error" in result
    status, result = _request(
        base,
        "/v1/tex-pdf",
        {"source": str(root / "escape.tex"), "output_dir": str(root / "out")},
    )
    assert status == 400 and "error" in result
    assert str(outside) not in json.dumps(result)
    assert _request(base, "/v1/tex-pdf", {"source": str(root / "missing.tex"), "output_dir": str(root / "out")})[0] == 400


def _fake_tool(path: Path, capture: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['TOOL_TEST_CAPTURE'], 'w') as f: json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, f)\n"
        "print('ok')\n"
    )
    path.chmod(0o755)
    return path


def test_document_commands_are_fixed_argument_lists_and_single_tex_pass(http_worker, monkeypatch):
    base, root = http_worker
    tool = _fake_tool(root / "fake-tool", root / "capture.json")
    monkeypatch.setenv("TOOL_TEST_CAPTURE", str(root / "capture.json"))
    monkeypatch.setenv("TOOL_XELATEX", str(tool))
    monkeypatch.setenv("TOOL_PANDOC", str(tool))
    monkeypatch.setenv("TOOL_SOFFICE", str(tool))

    tex_dir = root / "source"
    tex_dir.mkdir()
    tex = tex_dir / "main.tex"
    tex.write_text("\\documentclass{article}")
    output = root / "out"
    status, result = _request(base, "/v1/tex-pdf", {"source": str(tex), "output_dir": str(output)})
    assert status == 200 and result["returncode"] == 0
    call = json.loads((root / "capture.json").read_text())
    assert call["cwd"] == str(tex_dir)
    assert call["argv"] == ["-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", f"-output-directory={output}", "./main.tex"]

    markdown = root / "input.md"
    target = root / "out.docx"
    markdown.write_text("# example")
    assert _request(base, "/v1/pandoc", {"source": str(markdown), "target": str(target)})[0] == 200
    call = json.loads((root / "capture.json").read_text())
    assert call["argv"] == ["--from=markdown+tex_math_dollars-raw_html", "--to=docx", "--output", str(target), str(markdown)]

    docx = root / "input.docx"
    docx.write_bytes(b"synthetic")
    profile = root / "profile"
    assert _request(
        base,
        "/v1/word-pdf",
        {"source": str(docx), "output_dir": str(output), "profile_dir": str(profile)},
    )[0] == 200
    call = json.loads((root / "capture.json").read_text())
    assert call["argv"] == ["--headless", f"-env:UserInstallation={profile.as_uri()}", "--convert-to", "pdf", "--outdir", str(output), str(docx)]


def test_oversized_tool_logs_force_failure_and_are_bounded(http_worker, monkeypatch):
    base, root = http_worker
    tool = root / "verbose-tool"
    tool.write_text("#!/usr/bin/env python3\nprint('x' * 20000)\n")
    tool.chmod(0o755)
    monkeypatch.setenv("TOOL_PANDOC", str(tool))
    markdown = root / "input.md"
    markdown.write_text("# example")
    status, result = _request(base, "/v1/pandoc", {"source": str(markdown), "target": str(root / "out.docx")})
    assert status == 200
    assert result["returncode"] == 125
    assert len(result["stdout"]) <= worker.MAX_LOG_CHARS
    assert result["stderr"] == "tool output exceeded 16 KiB; result rejected"


def test_configured_symlink_executable_keeps_alias_for_argv0(http_worker, monkeypatch):
    base, root = http_worker
    target = root / "xetex-target"
    target.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['TOOL_TEST_CAPTURE'], 'w') as f: json.dump({'argv0': sys.argv[0]}, f)\n"
    )
    target.chmod(0o755)
    alias = root / "xelatex"
    alias.symlink_to(target)
    monkeypatch.setenv("TOOL_XELATEX", str(alias))
    monkeypatch.setenv("TOOL_TEST_CAPTURE", str(root / "capture.json"))
    source = root / "main.tex"
    source.write_text("\\documentclass{article}")

    status, result = _request(base, "/v1/tex-pdf", {"source": str(source), "output_dir": str(root / "out")})
    assert status == 200 and result["returncode"] == 0
    assert json.loads((root / "capture.json").read_text())["argv0"] == str(alias)


def test_tex_filename_cannot_override_fixed_options_over_http(http_worker, monkeypatch):
    base, root = http_worker
    tool = _fake_tool(root / "fake-xelatex", root / "capture.json")
    monkeypatch.setenv("TOOL_XELATEX", str(tool))
    monkeypatch.setenv("TOOL_TEST_CAPTURE", str(root / "capture.json"))
    source = root / "-shell-escape.tex"
    source.write_text("\\documentclass{article}")

    status, result = _request(base, "/v1/tex-pdf", {"source": str(source), "output_dir": str(root / "out")})
    assert status == 200 and result["returncode"] == 0
    args = json.loads((root / "capture.json").read_text())["argv"]
    assert args[:1] == ["-no-shell-escape"]
    assert args[-1] == "./-shell-escape.tex"
    assert "-shell-escape" not in args[:-1]


def _png(width: int, height: int) -> bytes:
    # The screenshot mock only needs a PNG signature and IHDR for the service's coverage check.
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"


class _Route:
    def __init__(self, url: str, events: list[str]):
        self.request = SimpleNamespace(url=url)
        self.events = events

    def continue_(self):
        self.events.append("continued")

    def abort(self):
        self.events.append("aborted")


def _install_fake_playwright(monkeypatch, *, external_request: bool):
    events: list[str] = []

    class Page:
        def set_default_timeout(self, _timeout):
            pass

        def set_content(self, _html, **_kwargs):
            assert route_handler is not None
            route_handler(_Route("data:text/html,ok", events))
            if external_request:
                route_handler(_Route("https://example.invalid/track", events))
                route_handler(_Route("file:///etc/passwd", events))

        def evaluate(self, script):
            if "document.fonts.ready" in script:
                return True
            return {"width": 1100, "height": 1200, "overflow": False}

        @property
        def locator(self):
            return lambda _selector: SimpleNamespace(evaluate_all=lambda _script: [])

        def set_viewport_size(self, _size):
            pass

        def screenshot(self, **_kwargs):
            return _png(1100, 1200)

    class Context:
        def route(self, _pattern, handler):
            nonlocal route_handler
            route_handler = handler

        def new_page(self):
            return Page()

    class Browser:
        def new_context(self, **_kwargs):
            return Context()

        def close(self):
            pass

    class Chromium:
        def launch(self, **_kwargs):
            return Browser()

    route_handler = None
    sync_api = SimpleNamespace(sync_playwright=lambda: _PlaywrightContext(Chromium()))
    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace(sync_api=sync_api))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return events


class _PlaywrightContext:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_screenshot_blocks_network_and_returns_covered_png(http_worker, monkeypatch, tmp_path):
    base, _root = http_worker
    browser = tmp_path / "chromium"
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setenv("TOOL_CHROMIUM", str(browser))

    events = _install_fake_playwright(monkeypatch, external_request=False)
    status, result = _request(base, "/v1/screenshot", {"html": "<main>safe</main>"})
    assert status == 200
    png = base64.b64decode(result["png_base64"])
    assert result["version"] == "tools-v1" and (result["width"], result["height"]) == (1100, 1200)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert events == ["continued"]

    events = _install_fake_playwright(monkeypatch, external_request=True)
    status, result = _request(base, "/v1/screenshot", {"html": "<main>external</main>"})
    assert status == 400 and result == {"error": "external browser requests are forbidden"}
    assert events == ["continued", "aborted", "aborted"]


def test_request_size_limit_is_enforced_over_http(http_worker):
    base, _root = http_worker
    address = base.removeprefix("http://")
    connection = http.client.HTTPConnection(address, timeout=5)
    connection.putrequest("POST", "/v1/screenshot")
    connection.putheader("Content-Length", str(worker.MAX_REQUEST_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 413
    connection.close()
