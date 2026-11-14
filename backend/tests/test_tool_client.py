from __future__ import annotations

import base64
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest
from app import semantic_inputs
from app.exporting import ExportError, _convert_pdf, _pandoc_formula
from app.tool_client import ToolClientError, convert_pandoc
from PIL import Image


@pytest.fixture
def tools_server(monkeypatch):
    calls: list[tuple[str, dict]] = []
    route = {"/v1/word-pdf": {}, "/v1/pandoc": {}, "/v1/screenshot": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            override = route.get(self.path)
            if override is None:
                self.send_error(404)
                return
            if "response" in override:
                result = override["response"]
            elif self.path == "/v1/word-pdf":
                out = Path(body["output_dir"]) / (Path(body["source"]).stem + ".pdf")
                out.write_bytes(b"%PDF-1.7 remote")
                result = {"version": "tools-v1", "returncode": 0, "stdout": "", "stderr": ""}
            elif self.path == "/v1/pandoc":
                target = Path(body["target"])
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(target, "w") as archive:
                    archive.writestr(
                        "word/document.xml",
                        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
                        '<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:document>',
                    )
                result = {"version": "tools-v1", "returncode": 0, "stdout": "", "stderr": ""}
            else:
                image = BytesIO()
                Image.new("RGB", (1100, 600), "white").save(image, format="PNG")
                result = {
                    "version": "tools-v1",
                    "png_base64": base64.b64encode(image.getvalue()).decode("ascii"),
                    "width": 1100,
                    "height": 600,
                }
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TEASSISTANT_TOOLS_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield calls, route
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_client_rejects_schema_version_mismatch(tmp_path, tools_server):
    _, route = tools_server
    route["/v1/pandoc"]["response"] = {
        "version": "tools-v2",
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }
    source = tmp_path / "formula.md"
    target = tmp_path / "formula.docx"
    source.write_text("$$x$$", encoding="utf-8")

    with pytest.raises(ToolClientError, match="unsupported schema version"):
        convert_pandoc(source, target)


def test_remote_transport_failure_does_not_fall_back_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("TEASSISTANT_TOOLS_URL", "http://127.0.0.1:1")
    monkeypatch.setattr("app.exporting.subprocess.run", lambda *_a, **_k: pytest.fail("local fallback"))
    source = tmp_path / "student.docx"
    source.write_bytes(b"docx")
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(ExportError, match="remote tools request"):
        _convert_pdf(source, out, "soffice", tmp_path / "profile")
    assert not (out / "student.pdf").exists()


def test_pdf_and_formula_use_remote_command_branches(tmp_path, monkeypatch, tools_server):
    calls, _ = tools_server
    monkeypatch.setattr("app.exporting.subprocess.run", lambda *_a, **_k: pytest.fail("local fallback"))
    docx = tmp_path / "student.docx"
    docx.write_bytes(b"docx")
    out = tmp_path / "out"
    out.mkdir()
    pdf = _convert_pdf(docx, out, "soffice", tmp_path / "profile")
    assert pdf.read_bytes().startswith(b"%PDF-")

    formula_dir = tmp_path / "formula-work"
    formula_dir.mkdir()
    node = _pandoc_formula("x+1", "pandoc", formula_dir, {})
    assert node.tag.endswith("oMath")
    assert [path for path, _ in calls] == ["/v1/word-pdf", "/v1/pandoc"]
    assert Path(calls[0][1]["source"]).is_absolute()
    assert Path(calls[0][1]["profile_dir"]).is_absolute()
    assert Path(calls[1][1]["target"]).is_absolute()


def test_remote_screenshot_checks_png_dimensions_and_tiles(tmp_path, tools_server):
    calls, _ = tools_server
    output = tmp_path / "images"
    output.mkdir()
    tiles = semantic_inputs._render_remote_page(
        "<html><body>example</body></html>", output_dir=output, qindex=1
    )
    assert len(tiles) == 1
    assert tiles[0]["width"] == 1100
    assert (output.parent / tiles[0]["path"]).is_file()
    assert calls[0][0] == "/v1/screenshot"

    _, route = tools_server
    image = BytesIO()
    Image.new("RGB", (1100, 600), "white").save(image, format="PNG")
    route["/v1/screenshot"]["response"] = {
        "version": "tools-v1",
        "png_base64": base64.b64encode(image.getvalue()).decode("ascii"),
        "width": 4,
        "height": 4,
    }
    with pytest.raises(ValueError, match="dimensions do not match"):
        semantic_inputs._render_remote_page(
            "<html></html>", output_dir=output, qindex=2
        )
