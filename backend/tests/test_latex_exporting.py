from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from app.exporting import ConverterUnavailable, ExportError
from app.latex_contracts import FormulaCatalog, FormulaConversion, validate_math_latex
from app.latex_exporting import (
    _compile_pdf,
    _is_short_choice_or_fill_blank,
    _is_short_table_question,
    _LatexRenderer,
    _tex_text,
    export_latex_snapshot,
)
from app.semantic_bank import canonical_hash


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _catalog(snapshot, bank, *, conversion=None):
    entries = {} if conversion is None else {conversion.source_sha256: conversion}
    return FormulaCatalog(
        snapshot_id=canonical_hash(snapshot.model_dump(mode="json")),
        bank_sha256=snapshot.bank_sha256,
        entries=entries,
    )


def _fake_xelatex(tmp_path: Path, *, output: str = "%PDF-1.4\nsynthetic PDF\n") -> Path:
    executable = tmp_path / "xelatex-fake"
    counter = tmp_path / "xelatex-call-count"
    source = (
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"counter=Path({str(counter)!r})\n"
        "counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')\n"
        "out=Path(next(arg.split('=',1)[1] for arg in sys.argv if arg.startswith('-output-directory=')))\n"
        "tex=Path(sys.argv[-1])\n"
        "out.mkdir(parents=True,exist_ok=True)\n"
        f"(out/(tex.stem+'.pdf')).write_bytes({output.encode()!r})\n"
        + ("print('Overfull \\\\hbox (2.0pt too wide)')\n" if "Overfull" in output else "")
    )
    executable.write_text(source, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _fixture(tmp_path, *, image=False):
    from test_source_exporting import _make_fixture

    return _make_fixture(tmp_path, image=image)


def _mock_catalog_validation(monkeypatch):
    import app.math_latex

    monkeypatch.setattr(app.math_latex, "validate_formula_catalog", lambda *_: None)


def test_latex_export_uses_converted_omml_and_preserves_tables_transactionally(
    tmp_path, monkeypatch
):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _fixture(tmp_path)
    source_xml = snapshot.items[0].question.source_payload["stem_blocks"][0]["omml_occurrences"][0][
        "source_xml"
    ]
    conversion = FormulaConversion(
        source_kind="omml",
        source_sha256=_sha(source_xml.encode("utf-8")),
        status="converted",
        latex=r"\frac{1}{2}+\frac{1}{2}",
        mathml="<math/>",
        converter="synthetic-omml-v1",
    )
    catalog = _catalog(snapshot, bank, conversion=conversion)
    _mock_catalog_validation(monkeypatch)
    executable = _fake_xelatex(tmp_path)
    output = tmp_path / "latex-export"

    result = export_latex_snapshot(
        snapshot,
        output,
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        formula_catalog=catalog,
        xelatex_executable=executable,
        layout="compact",
    )

    assert result["layout"] == "compact"
    assert set(result["files"]) == {"student_tex", "student_pdf", "teacher_tex", "teacher_pdf"}
    assert Path(result["files"]["student_pdf"]).read_bytes().startswith(b"%PDF-")
    assert (output / "manifest.json").is_file()
    assert Path(tmp_path / "xelatex-call-count").read_text() == "4"
    student_tex = Path(result["files"]["student_tex"]).read_text()
    teacher_tex = Path(result["files"]["teacher_tex"]).read_text()
    manifest = json.loads((output / "manifest.json").read_text())
    assert r"\(\frac{1}{2}+\frac{1}{2}\)" in student_tex
    assert r"\(1\)" in student_tex and r"\(2\)" in student_tex
    assert "0.5" in student_tex
    assert "42" in teacher_tex and "保留小数" not in teacher_tex
    assert manifest["counts"]["student"]["omml_formula_count"] == 1
    assert manifest["counts"]["student"]["table_count"] == 1
    assert manifest["teacher_content"] == "answers_and_solutions"


@pytest.mark.skipif(shutil.which("xelatex") is None, reason="XeLaTeX is not installed")
def test_synthetic_snapshot_compiles_with_real_xelatex(tmp_path, monkeypatch):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _fixture(tmp_path)
    source_xml = snapshot.items[0].question.source_payload["stem_blocks"][0]["omml_occurrences"][0][
        "source_xml"
    ]
    conversion = FormulaConversion(
        source_kind="omml",
        source_sha256=_sha(source_xml.encode("utf-8")),
        status="converted",
        latex=r"\frac{1}{2}+\sqrt{2}",
        mathml="<math/>",
        converter="synthetic-omml-v1",
    )
    catalog = _catalog(snapshot, bank, conversion=conversion)
    _mock_catalog_validation(monkeypatch)
    result = export_latex_snapshot(
        snapshot,
        tmp_path / "real-xelatex",
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        formula_catalog=catalog,
        xelatex_executable=shutil.which("xelatex"),
    )
    assert Path(result["files"]["student_pdf"]).read_bytes().startswith(b"%PDF-")
    assert Path(result["files"]["teacher_pdf"]).read_bytes().startswith(b"%PDF-")


@pytest.mark.skipif(shutil.which("xelatex") is None, reason="XeLaTeX is not installed")
def test_known_geometry_symbols_compile_as_math(tmp_path):
    tex_path = tmp_path / "symbols.tex"
    tex_path.write_text(
        "\\documentclass[UTF8,fontset=fandol]{ctexart}\n"
        "\\usepackage{fontspec}\n"
        "\\usepackage{unicode-math}\n"
        "\\setmainfont{lmroman10-regular.otf}[BoldFont=lmroman10-bold.otf,"
        "ItalicFont=lmroman10-italic.otf,BoldItalicFont=lmroman10-bolditalic.otf]\n"
        "\\setmathfont{latinmodern-math.otf}\n"
        "\\begin{document}\n" + _tex_text("∠ ∴ ∵ ⊙ ⊥ △ （ ） ( )") + "\\end{document}\n",
        encoding="utf-8",
    )
    pdf_path = _compile_pdf(tex_path, tmp_path / "symbols-build", shutil.which("xelatex"))
    assert pdf_path.read_bytes().startswith(b"%PDF-")


def test_geometry_asset_is_copied_with_source_ratio_and_compiler_warnings_fail_closed(
    tmp_path, monkeypatch
):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _fixture(tmp_path, image=True)
    catalog = _catalog(snapshot, bank)
    _mock_catalog_validation(monkeypatch)
    output = tmp_path / "image-latex"
    result = export_latex_snapshot(
        snapshot,
        output,
        bank_dir=bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=progress,
        formula_catalog=catalog,
        xelatex_executable=_fake_xelatex(tmp_path),
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"]["student"]["geometry_asset_count"] == 1
    asset = output / manifest["assets"][0]["path"]
    original = bank_dir / "media/doc1/picture.png"
    assert asset.read_bytes() == original.read_bytes()
    tex = Path(result["files"]["student_tex"]).read_text()
    assert r"\sourceimage{assets/geometry-" in tex
    assert "{15.000}{7.500}" in tex

    warning_output = tmp_path / "warned-latex"
    with pytest.raises(ExportError, match="overfull TeX layout"):
        export_latex_snapshot(
            snapshot,
            warning_output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=catalog,
            xelatex_executable=_fake_xelatex(tmp_path, output="%PDF-1.4\nOverfull\n"),
        )
    assert not warning_output.exists()


def test_unsupported_or_missing_formula_catalog_never_publishes(tmp_path, monkeypatch):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _fixture(tmp_path)
    source_xml = snapshot.items[0].question.source_payload["stem_blocks"][0]["omml_occurrences"][0][
        "source_xml"
    ]
    source_hash = _sha(source_xml.encode("utf-8"))
    unsupported = FormulaConversion(
        source_kind="omml",
        source_sha256=source_hash,
        status="unsupported",
        latex=None,
        converter="synthetic-omml-v1",
        errors=["unsupported_structure"],
    )
    _mock_catalog_validation(monkeypatch)
    executable = _fake_xelatex(tmp_path)
    unsupported_output = tmp_path / "unsupported-export"
    with pytest.raises(ExportError, match="unsupported"):
        export_latex_snapshot(
            snapshot,
            unsupported_output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=_catalog(snapshot, bank, conversion=unsupported),
            xelatex_executable=executable,
        )
    assert not unsupported_output.exists()

    missing_output = tmp_path / "missing-export"
    with pytest.raises(ExportError, match="missing a source occurrence"):
        export_latex_snapshot(
            snapshot,
            missing_output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=_catalog(snapshot, bank),
            xelatex_executable=executable,
        )
    assert not missing_output.exists()


def test_formula_catalog_identity_and_latex_whitelist_fail_before_publish(tmp_path, monkeypatch):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _fixture(tmp_path)
    _mock_catalog_validation(monkeypatch)
    wrong_catalog = FormulaCatalog(
        snapshot_id="wrong-snapshot",
        bank_sha256=snapshot.bank_sha256,
        entries={},
    )
    with pytest.raises(ExportError, match="different practice snapshot"):
        export_latex_snapshot(
            snapshot,
            tmp_path / "wrong-catalog",
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=wrong_catalog,
            xelatex_executable=_fake_xelatex(tmp_path),
        )
    with pytest.raises(ValueError):
        validate_math_latex(r"\input{secret}")
    assert _tex_text("x_1 & 50%") == r"x\_1 \& 50\%"
    assert _tex_text("∠ ∴ ∵ ⊙ ⊥ △") == (
        r"\(\angle\) \(\therefore\) \(\because\) \(\odot\) \(\perp\) \(\triangle\)"
    )
    assert _tex_text("（ ） ( )") == r"\mbox{（\hspace{1em}）} \mbox{(\hspace{1em})}"


def test_short_table_question_guard_is_bounded():
    blocks = [
        {
            "html": "<p>根据下表回答问题</p><table><tr><td>a</td></tr>"
            "<tr><td>b</td></tr><tr><td>c</td></tr></table><p>结果保留一位小数。</p>",
            "text": "根据下表回答问题\n结果保留一位小数。",
        }
    ]
    assert _is_short_table_question(blocks)
    assert not _is_short_table_question(blocks * 6)


def test_formula_preview_count_does_not_exclude_short_diagram_choice():
    html = "<p>A. x " + " ".join('<img src="formula.png">' for _ in range(10)) + "</p>"
    block = {
        "html": html,
        "text": "A. x",
        "media_occurrences": [
            *({"kind": "ole_preview"} for _ in range(10)),
            {"kind": "drawingml"},
        ],
    }
    assert _is_short_choice_or_fill_blank("single_choice", [block])
    block["media_occurrences"].extend({"kind": "drawingml"} for _ in range(4))
    assert not _is_short_choice_or_fill_blank("single_choice", [block])


def test_ordered_inline_option_layout_and_repeated_media_path_matching(tmp_path):
    catalog = FormulaCatalog(snapshot_id="s", bank_sha256="b", entries={})
    renderer = _LatexRenderer(
        bank_root=tmp_path,
        documents={},
        asset_hashes={},
        catalog=catalog,
        stage=tmp_path,
    )
    context = {"option_columns": 2, "option_next": 0}
    rendered = renderer._render_text("\u00a0A. x\u00a0 B. y\u00a0 C. z\u00a0 D. w", context)
    assert r"A. x\hspace{\fill}B. y\\[1mm]C. z\hspace{\fill}D. w" in rendered

    media_context = {
        "used_media": set(),
        "media": [
            {"media_path": "media/shared.png", "source_rel_id": "rId1"},
            {"media_path": "media/shared.png", "source_rel_id": "rId2"},
        ],
    }
    assert renderer._find_media(media_context, "media/shared.png") == 0
    media_context["used_media"].add(0)
    assert renderer._find_media(media_context, "media/shared.png") == 1


def test_missing_xelatex_is_explicit(tmp_path, monkeypatch):
    bank_dir, bank, curriculum, progress, snapshot, _, _ = _fixture(tmp_path)
    _mock_catalog_validation(monkeypatch)
    with pytest.raises(ConverterUnavailable):
        export_latex_snapshot(
            snapshot,
            tmp_path / "missing-xelatex",
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=_catalog(snapshot, bank),
            xelatex_executable=tmp_path / "not-found",
        )
    assert not (tmp_path / "missing-xelatex").exists()
