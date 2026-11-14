from __future__ import annotations

import base64
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "tools_smoke_fixture", ROOT / "scripts" / "container_smoke_fixture.py"
)
assert SPEC and SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def _png(width: int = 1100, height: int = 600) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _docx(path: Path, *, geometry: bool) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><m:oMath/></w:document>",
        )
        if geometry:
            archive.writestr("word/media/image1.png", b"synthetic")


def _remote(monkeypatch) -> None:
    monkeypatch.setenv("TEASSISTANT_TOOLS_URL", "http://tools:8001")


def test_remote_word_fixture_uses_business_exporter_without_local_tool_lookup(
    tmp_path, monkeypatch
):
    _remote(monkeypatch)
    monkeypatch.setattr(
        fixture.shutil,
        "which",
        lambda _name: pytest.fail("remote fixture checked for a local converter"),
    )

    def fake_run(command, *, cwd, timeout):
        assert cwd == fixture.ROOT and timeout == 180
        assert command[command.index("--pandoc") + 1] == "pandoc"
        assert command[command.index("--soffice") + 1] == "soffice"
        output = Path(command[command.index("--output") + 1])
        output.mkdir()
        _docx(output / "student.docx", geometry=True)
        _docx(output / "teacher.docx", geometry=False)
        (output / "student.pdf").write_bytes(b"%PDF-1.7 student")
        (output / "teacher.pdf").write_bytes(b"%PDF-1.7 teacher")
        return "synthetic remote export complete"

    monkeypatch.setattr(fixture, "_run", fake_run)
    monkeypatch.setattr(fixture, "_pdf_text", lambda _path: "合成数学方程")
    monkeypatch.setattr(fixture, "_pdf_text_pages", lambda _path: ["合成数学方程"])
    report = fixture.export_word(tmp_path / "word")

    assert report["synthetic_marker"] == "开发验证样张（合成学生进度）"
    assert len(report["docx"]) == 2 and len(report["word_pdfs"]) == 2
    assert report["docx"][0]["native_math"] is True


def test_remote_latex_fixture_delegates_compile_and_keeps_pdf_checks(tmp_path, monkeypatch):
    _remote(monkeypatch)
    monkeypatch.setattr(
        fixture.shutil,
        "which",
        lambda _name: pytest.fail("remote fixture checked for local XeLaTeX"),
    )
    calls = []

    def fake_compile(source, build_dir, executable):
        calls.append((source, build_dir, executable))
        pdf = build_dir / f"{source.stem}.pdf"
        pdf.write_bytes(b"%PDF-1.7 synthetic")
        return pdf

    from app import latex_exporting

    monkeypatch.setattr(latex_exporting, "_compile_pdf", fake_compile)
    monkeypatch.setattr(fixture, "_pdf_text", lambda _path: "开发容器冒烟样例 x 方程")
    monkeypatch.setattr(fixture, "_pdf_text_pages", lambda _path: ["开发容器冒烟样例 x 方程"])

    report = fixture.export_latex(tmp_path / "latex")

    assert calls == [(tmp_path / "latex" / "synthetic.tex", tmp_path / "latex", "xelatex")]
    assert report["no_shell_escape"] is True
    assert report["missing_glyph_or_overfull"] is False
    assert (tmp_path / "latex" / "synthetic-geometry.png").is_file()


def test_remote_chromium_fixture_validates_png_without_playwright(tmp_path, monkeypatch):
    _remote(monkeypatch)
    monkeypatch.setattr(
        fixture.shutil,
        "which",
        lambda _name: pytest.fail("remote fixture checked for Chromium"),
    )
    calls = []

    def screenshot(markup):
        calls.append(markup)
        return _png(), 1100, 600

    from app import tool_client

    monkeypatch.setattr(tool_client, "render_screenshot", screenshot)
    report = fixture.render_chromium(tmp_path / "chromium")

    assert report == {
        "synthetic": True,
        "screenshot_bytes": len(_png()),
        "math_elements": 1,
        "external_network": False,
    }
    assert len(calls) == 1
    assert calls[0].count("<math ") == 1
    assert "data:image/png;base64," in calls[0]


def test_remote_chromium_fixture_rejects_png_dimension_mismatch(tmp_path, monkeypatch):
    _remote(monkeypatch)

    from app import tool_client

    monkeypatch.setattr(
        tool_client,
        "render_screenshot",
        lambda _markup: (base64.b64decode(base64.b64encode(_png())), 1, 1),
    )

    with pytest.raises(RuntimeError, match="dimensions are invalid"):
        fixture.render_chromium(tmp_path / "chromium")
