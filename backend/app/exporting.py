"""Transactional DOCX/PDF export for immutable practice snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.shared import Inches, Pt, RGBColor
from PIL import Image

from app.contracts import (
    ContentNode,
    Curriculum,
    PracticeSnapshot,
    Question,
    StudentProgress,
    Subquestion,
)
from app.practice import validate_current_progress

_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M_OMATH = f"{{{_M_NS}}}oMath"


class ExportError(RuntimeError):
    """A requested export could not be completed without losing content."""


class ConverterUnavailable(ExportError):
    """A required external document converter is unavailable."""


def _available_executable(executable: str | Path | None) -> str | None:
    if executable is None:
        return None
    value = str(executable)
    if os.path.sep in value or (os.path.altsep and os.path.altsep in value):
        path = Path(value).expanduser().absolute()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(value)


@dataclass(frozen=True)
class ExportResult:
    snapshot_id: str
    current_progress_revision: str
    curriculum_version: str
    progress_validation: str
    student_docx: Path
    student_pdf: Path
    teacher_docx: Path
    teacher_pdf: Path


def _resolve_asset(asset_path: str, asset_root: Path) -> Path:
    """Resolve a local asset and enforce containment after following symlinks."""
    if "://" in asset_path or asset_path.startswith(("//", "\\\\")):
        raise ExportError("remote image assets are not supported")
    root = asset_root.resolve(strict=True)
    candidate = (root / asset_path).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ExportError(f"image asset escapes the configured asset root: {asset_path}") from exc
    if not candidate.is_file():
        raise ExportError(f"image asset is not a regular file: {asset_path}")
    return candidate


def _validate_nodes(nodes: Iterable[ContentNode], asset_root: Path) -> None:
    for node in nodes:
        if node.kind == "image":
            assert node.asset_path is not None
            _resolve_asset(node.asset_path, asset_root)
        elif node.kind == "formula":
            if not node.latex or not node.latex.strip():
                raise ExportError("formula content cannot be empty")


def _validate_question(question: Question, asset_root: Path) -> None:
    for nodes in (question.stem, question.answer, question.solution):
        _validate_nodes(nodes, asset_root)
    for subquestion in question.subquestions:
        _validate_subquestion(subquestion, asset_root)


def _validate_subquestion(subquestion: Subquestion, asset_root: Path) -> None:
    _validate_nodes(subquestion.stem, asset_root)
    _validate_nodes(subquestion.answer, asset_root)


def _pandoc_formula(
    latex: str, pandoc: str, workdir: Path, cache: dict[str, ElementTree.Element]
) -> ElementTree.Element:
    """Return a parsed OMML node for a single formula, failing closed otherwise."""
    if "$" in latex or r"\(" in latex or r"\)" in latex or r"\[" in latex or r"\]" in latex:
        raise ExportError(
            "formula contains a math delimiter and could escape its isolated math context"
        )
    if latex in cache:
        return deepcopy(cache[latex])
    source = workdir / f"formula-{len(cache)}.md"
    target = workdir / f"formula-{len(cache)}.docx"
    # Raw HTML is disabled; text nodes never pass through Pandoc at all.
    source.write_text(f"$${latex}$$\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                pandoc,
                "--from=markdown+tex_math_dollars-raw_html",
                "--to=docx",
                "--output",
                str(target),
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExportError(f"Pandoc formula conversion failed: {exc}") from exc
    if completed.returncode != 0 or completed.stderr.strip():
        detail = completed.stderr.strip()[-1000:]
        reason = "reported a warning" if completed.returncode == 0 else "could not convert"
        raise ExportError(f"Pandoc {reason} formula {latex!r}: {detail}")
    try:
        with zipfile.ZipFile(target) as archive:
            xml = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ExportError(f"Pandoc produced an unreadable DOCX for formula {latex!r}") from exc
    math_nodes = list(xml.iter(_M_OMATH))
    residual_text = [
        element.text.strip()
        for element in xml.iter(f"{{{_W_NS}}}t")
        if element.text and element.text.strip()
    ]
    if len(math_nodes) != 1 or residual_text:
        raise ExportError(
            f"Pandoc formula conversion was incomplete for {latex!r}: "
            f"OMML nodes={len(math_nodes)}, residual text={residual_text!r}"
        )
    if not "".join(math_nodes[0].itertext()).strip():
        raise ExportError(f"Pandoc produced empty editable OMML for formula {latex!r}")
    node = parse_xml(ElementTree.tostring(math_nodes[0], encoding="utf-8"))
    cache[latex] = node
    return deepcopy(node)


def _append_nodes(
    paragraph,
    nodes: Iterable[ContentNode],
    asset_root: Path,
    pandoc: str,
    workdir: Path,
    formula_cache: dict[str, ElementTree.Element],
) -> None:
    for node in nodes:
        if node.kind == "text":
            assert node.text is not None
            paragraph.add_run(node.text)
        elif node.kind == "formula":
            assert node.latex is not None
            paragraph._p.append(_pandoc_formula(node.latex, pandoc, workdir, formula_cache))
        else:
            assert node.asset_path is not None
            image_path = _resolve_asset(node.asset_path, asset_root)
            with Image.open(image_path) as image:
                pixel_width, pixel_height = image.size
            scale = min(5.8 / pixel_width, 6.0 / pixel_height)
            paragraph.add_run().add_picture(
                str(image_path),
                width=Inches(pixel_width * scale),
                height=Inches(pixel_height * scale),
            )


def _content_paragraph(
    document,
    nodes: Iterable[ContentNode],
    asset_root: Path,
    pandoc: str,
    workdir: Path,
    formula_cache: dict[str, ElementTree.Element],
):
    paragraph = document.add_paragraph()
    _append_nodes(paragraph, nodes, asset_root, pandoc, workdir, formula_cache)
    return paragraph


def _format_document(document, title: str) -> None:
    section = document.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)
    normal = document.styles["Normal"]
    normal.font.name = "Noto Sans CJK SC"
    normal.font.size = Pt(11)
    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = document.styles[style_name]
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.underline = False
    # Built-in Word templates can inherit a blue rule from paragraph styles.
    # Removing only the title paragraph's direct border leaves that rule in exported PDF.
    for border in list(document.styles.element.iter(f"{{{_W_NS}}}pBdr")):
        border.getparent().remove(border)
    heading = document.add_paragraph(style="Title")
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_borders = heading._p.pPr.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr"
    )
    if p_borders is not None:
        heading._p.pPr.remove(p_borders)
    heading.add_run(title)


def _question_has_teacher_content(question: Question) -> bool:
    return bool(question.answer or question.solution or question.subquestions)


def _write_student(
    snapshot: PracticeSnapshot,
    destination: Path,
    asset_root: Path,
    pandoc: str,
    workdir: Path,
    formula_cache: dict[str, ElementTree.Element],
    point_names: dict[str, str],
) -> None:
    document = Document()
    _format_document(document, "数学练习卷")
    active_point = None
    for item in snapshot.items:
        if item.target_point_id != active_point:
            document.add_heading(
                point_names.get(item.target_point_id, item.target_point_id), level=1
            )
            active_point = item.target_point_id
        question = item.question
        document.add_heading(f"{item.order}.", level=2)
        _content_paragraph(document, question.stem, asset_root, pandoc, workdir, formula_cache)
        for sub_index, subquestion in enumerate(question.subquestions, start=1):
            paragraph = document.add_paragraph()
            paragraph.add_run(f"（{sub_index}）")
            _append_nodes(paragraph, subquestion.stem, asset_root, pandoc, workdir, formula_cache)
    document.save(destination)


def _write_teacher(
    snapshot: PracticeSnapshot,
    destination: Path,
    asset_root: Path,
    pandoc: str,
    workdir: Path,
    formula_cache: dict[str, ElementTree.Element],
    point_names: dict[str, str],
) -> None:
    document = Document()
    _format_document(document, "教师答案与解析")
    active_point = None
    for item in snapshot.items:
        if item.target_point_id != active_point:
            document.add_heading(
                point_names.get(item.target_point_id, item.target_point_id), level=1
            )
            active_point = item.target_point_id
        question = item.question
        document.add_heading(f"{item.order}.", level=2)
        if question.answer:
            paragraph = document.add_paragraph()
            paragraph.add_run("答案：").bold = True
            _append_nodes(paragraph, question.answer, asset_root, pandoc, workdir, formula_cache)
        if question.solution:
            paragraph = document.add_paragraph()
            paragraph.add_run("解析：").bold = True
            _append_nodes(paragraph, question.solution, asset_root, pandoc, workdir, formula_cache)
        for sub_index, subquestion in enumerate(question.subquestions, start=1):
            paragraph = document.add_paragraph()
            paragraph.add_run(f"（{sub_index}）答案：").bold = True
            _append_nodes(paragraph, subquestion.answer, asset_root, pandoc, workdir, formula_cache)
    document.save(destination)


def _convert_pdf(docx: Path, output_dir: Path, soffice: str, profile_dir: Path) -> Path:
    profile_dir.mkdir(parents=True, exist_ok=False)
    target = output_dir / f"{docx.stem}.pdf"
    try:
        completed = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation={profile_dir.as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(docx),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExportError(f"LibreOffice conversion failed for {docx.name}: {exc}") from exc
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size < 5:
        detail = completed.stderr.strip()[-1000:]
        raise ExportError(f"LibreOffice did not produce {target.name}: {detail}")
    with target.open("rb") as pdf_file:
        if pdf_file.read(5) != b"%PDF-":
            raise ExportError(f"LibreOffice output is not a PDF: {target.name}")
    return target


def export_snapshot(
    snapshot: PracticeSnapshot,
    output_dir: str | Path,
    *,
    asset_root: str | Path,
    curriculum: Curriculum,
    current_progress: StudentProgress,
    pandoc_executable: str | Path | None = None,
    soffice_executable: str | Path | None = None,
) -> ExportResult:
    """Create four files from one snapshot and publish them as an atomic directory.

    ``output_dir`` must not already exist. The staged directory is renamed into place
    only after both DOCX files and both PDFs have been generated successfully.
    """
    # Re-parse serialized data because callers may have mutated nested Pydantic lists.
    snapshot = PracticeSnapshot.model_validate(snapshot.model_dump(mode="python"))
    curriculum = Curriculum.model_validate(curriculum.model_dump(mode="python"))
    current_progress = StudentProgress.model_validate(current_progress.model_dump(mode="python"))
    try:
        validate_current_progress(snapshot, curriculum, current_progress)
    except ValueError as exc:
        raise ExportError(f"snapshot is incompatible with current student progress: {exc}") from exc
    if not snapshot.items:
        raise ExportError("cannot export an empty practice snapshot")

    final_dir = Path(output_dir).absolute()
    if final_dir.exists() or final_dir.is_symlink():
        raise ExportError(f"export destination already exists: {final_dir}")
    pandoc = (
        _available_executable(pandoc_executable)
        if pandoc_executable is not None
        else shutil.which("pandoc")
    )
    soffice = (
        _available_executable(soffice_executable)
        if soffice_executable is not None
        else shutil.which("soffice")
    )
    missing = [
        name for name, path in (("Pandoc", pandoc), ("LibreOffice soffice", soffice)) if not path
    ]
    if missing:
        raise ConverterUnavailable("required export converter unavailable: " + ", ".join(missing))

    assets = Path(asset_root)
    for item in snapshot.items:
        _validate_question(item.question, assets)
        if not _question_has_teacher_content(item.question):
            raise ExportError(f"question {item.question.id} has no teacher answer content")

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{final_dir.name}.staging-", dir=final_dir.parent))
    try:
        workdir = staging / ".work"
        workdir.mkdir()
        formula_cache: dict[str, ElementTree.Element] = {}
        point_names = {point.id: point.name for point in curriculum.points}
        student_docx = staging / "student.docx"
        teacher_docx = staging / "teacher.docx"
        _write_student(snapshot, student_docx, assets, pandoc, workdir, formula_cache, point_names)
        _write_teacher(snapshot, teacher_docx, assets, pandoc, workdir, formula_cache, point_names)
        student_pdf = _convert_pdf(student_docx, staging, soffice, workdir / "lo-profile-student")
        teacher_pdf = _convert_pdf(teacher_docx, staging, soffice, workdir / "lo-profile-teacher")
        shutil.rmtree(workdir)
        # Recheck immediately before publication; never intentionally overwrite output.
        if final_dir.exists() or final_dir.is_symlink():
            raise ExportError(f"export destination appeared during export: {final_dir}")
        os.rename(staging, final_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ExportResult(
        snapshot_id=hashlib.sha256(
            json.dumps(
                snapshot.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        current_progress_revision=current_progress.revision,
        curriculum_version=curriculum.version,
        progress_validation="passed",
        student_docx=final_dir / student_docx.name,
        student_pdf=final_dir / student_pdf.name,
        teacher_docx=final_dir / teacher_docx.name,
        teacher_pdf=final_dir / teacher_pdf.name,
    )
