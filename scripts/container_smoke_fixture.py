#!/usr/bin/env python3
"""Build and verify synthetic document/PDF exports inside the production image."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path, timeout: int) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"synthetic export command failed ({result.returncode})")
    return result.stdout


def _pdf_text(path: Path) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        return "\n".join(
            page.get_textpage().get_text_range()
            for page in (pdf[index] for index in range(len(pdf)))
        )
    finally:
        pdf.close()


def _verify_word_exports(directory: Path) -> dict[str, object]:
    docx = sorted(directory.glob("*.docx"))
    pdfs = sorted(directory.glob("*.pdf"))
    if len(docx) != 2 or len(pdfs) != 2:
        raise RuntimeError("synthetic Word export did not produce four expected files")
    document_checks = []
    for path in docx:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            xml = archive.read("word/document.xml")
            if b"oMath" not in xml:
                raise RuntimeError("synthetic DOCX lacks native math")
            has_geometry = any(name.startswith("word/media/") for name in names)
            if path.name == "student.docx" and not has_geometry:
                raise RuntimeError("synthetic student DOCX lacks geometry media")
            document_checks.append(
                {
                    "file": path.name,
                    "size_bytes": path.stat().st_size,
                    "native_math": True,
                    "geometry_media": has_geometry,
                }
            )
    pdf_checks = []
    for path in pdfs:
        if not path.read_bytes().startswith(b"%PDF-"):
            raise RuntimeError("synthetic Word export PDF signature is invalid")
        text = _pdf_text(path)
        if not any(char in text for char in "数学方程合成"):
            raise RuntimeError("synthetic Word PDF has no expected Chinese text")
        pdf_checks.append(
            {"size_bytes": path.stat().st_size, "page_count": len(_pdf_text_pages(path))}
        )
    return {"docx": document_checks, "word_pdfs": pdf_checks}


def _pdf_text_pages(path: Path) -> list[str]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        return [pdf[index].get_textpage().get_text_range() for index in range(len(pdf))]
    finally:
        pdf.close()


def export_word(output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError("smoke output must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    import pypandoc

    soffice = (
        os.environ.get("TEASSISTANT_SOFFICE")
        or shutil.which("libreoffice")
        or shutil.which("soffice")
    )
    pandoc = pypandoc.get_pandoc_path()
    command = [
        sys.executable,
        str(ROOT / "scripts" / "export_demo.py"),
        "--output",
        str(output),
        "--pandoc",
        pandoc,
        "--soffice",
        soffice or "",
    ]
    if not soffice:
        raise RuntimeError("LibreOffice is unavailable in the image")
    log = _run(command, cwd=ROOT, timeout=180)
    report = _verify_word_exports(output)
    report["synthetic_marker"] = "开发验证样张（合成学生进度）"
    report["export_log_lines"] = len(log.splitlines())
    return report


def _make_geometry_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (420, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.line((40, 185, 210, 35, 380, 185, 40, 185), fill=(20, 60, 120), width=5)
    draw.line((210, 35, 210, 185), fill=(180, 60, 45), width=3)
    image.save(path, format="PNG")


def export_latex(output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError("smoke output must be new")
    output.mkdir(parents=True)
    _make_geometry_png(output / "synthetic-geometry.png")
    tex = output / "synthetic.tex"
    tex.write_text(
        r"""\documentclass[UTF8,fontset=fandol]{ctexart}
\usepackage[margin=24mm]{geometry}
\usepackage{graphicx}
\pagestyle{empty}
\begin{document}
开发容器冒烟样例。方程：$x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}$。
\begin{center}\includegraphics[width=4cm]{synthetic-geometry.png}\end{center}
\end{document}
""",
        encoding="utf-8",
    )
    xelatex = shutil.which("xelatex")
    if not xelatex:
        raise RuntimeError("XeLaTeX is unavailable in the image")
    log = ""
    for _ in range(2):
        log = _run(
            [
                xelatex,
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                tex.name,
            ],
            cwd=output,
            timeout=90,
        )
    if re.search(r"Missing character:|Overfull \\hbox", log):
        raise RuntimeError("XeLaTeX smoke reported missing glyphs or overfull lines")
    pdf = output / "synthetic.pdf"
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        raise RuntimeError("XeLaTeX smoke did not produce a valid PDF")
    text = _pdf_text(pdf)
    if "开发容器冒烟样例" not in text or not any(token in text for token in ("x", "方程")):
        raise RuntimeError("XeLaTeX PDF is missing expected text or formula content")
    return {
        "tex_bytes": tex.stat().st_size,
        "pdf_bytes": pdf.stat().st_size,
        "page_count": len(_pdf_text_pages(pdf)),
        "geometry_png_bytes": (output / "synthetic-geometry.png").stat().st_size,
        "no_shell_escape": True,
        "missing_glyph_or_overfull": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("word", "latex"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export_word(args.output) if args.kind == "word" else export_latex(args.output)
    print(json.dumps({"status": "passed", "kind": args.kind, "checks": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
