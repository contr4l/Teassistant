import shutil
from pathlib import Path

import pytest
from app.contracts import (
    ContentNode,
    Curriculum,
    KnowledgePoint,
    PracticeItem,
    PracticeRequest,
    PracticeSnapshot,
    Question,
    Quota,
    SourceRegion,
    StudentProgress,
    Subquestion,
)
from app.exporting import (
    ConverterUnavailable,
    ExportError,
    _append_nodes,
    _pandoc_formula,
    _resolve_asset,
    export_snapshot,
)
from docx import Document
from PIL import Image


def _snapshot() -> PracticeSnapshot:
    question = Question(
        id="q1",
        canonical_id="same-q1",
        content_fingerprint="hash-q1",
        curriculum_version="curriculum-v1",
        stem=[
            ContentNode(kind="text", text="已知方程，求未知数："),
            ContentNode(kind="formula", latex="x^2-5x+6=0"),
        ],
        answer=[ContentNode(kind="text", text="x=2 或 x=3")],
        subquestions=[
            Subquestion(
                id="q1-a",
                stem=[
                    ContentNode(kind="text", text="求方程组："),
                    ContentNode(kind="formula", latex=r"\begin{cases}x+y=5\\x-y=1\end{cases}"),
                ],
                answer=[ContentNode(kind="formula", latex="x=3,\\quad y=2")],
            )
        ],
        point_ids=["linear-equations"],
        difficulty_score=40,
        sources=[SourceRegion(asset_id="source1", page=1, bbox=(0, 0, 1, 1))],
    )
    return PracticeSnapshot(
        student_id="synthetic-student",
        progress_revision="progress-v1",
        curriculum_version="curriculum-v1",
        request=PracticeRequest(quotas=[Quota(point_id="linear-equations", count=1)]),
        items=[PracticeItem(order=1, target_point_id="linear-equations", question=question)],
    )


def _curriculum_and_progress() -> tuple[Curriculum, StudentProgress]:
    return (
        Curriculum(
            version="curriculum-v1",
            points=[KnowledgePoint(id="linear-equations", name="一元一次方程")],
        ),
        StudentProgress(
            student_id="synthetic-student",
            revision="current-progress-v1",
            curriculum_version="curriculum-v1",
            learned_point_ids=["linear-equations"],
        ),
    )


def test_missing_pandoc_fails_explicitly_without_publishing(tmp_path: Path) -> None:
    destination = tmp_path / "export"

    with pytest.raises(ConverterUnavailable, match="Pandoc"):
        export_snapshot(
            _snapshot(),
            destination,
            asset_root=tmp_path,
            curriculum=_curriculum_and_progress()[0],
            current_progress=_curriculum_and_progress()[1],
            pandoc_executable=tmp_path / "missing-pandoc",
            soffice_executable=tmp_path / "missing-soffice",
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_export_revalidates_mutated_nested_snapshot_before_converter_lookup(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot.items[0].question.stem.clear()

    with pytest.raises(ValueError, match="at least 1 item"):
        export_snapshot(
            snapshot,
            tmp_path / "export",
            asset_root=tmp_path,
            curriculum=_curriculum_and_progress()[0],
            current_progress=_curriculum_and_progress()[1],
            pandoc_executable=tmp_path / "missing-pandoc",
            soffice_executable=tmp_path / "missing-soffice",
        )


def test_export_rejects_empty_snapshot(tmp_path: Path) -> None:
    snapshot = _snapshot().model_copy(
        update={
            "request": PracticeRequest(quotas=[Quota(point_id="linear-equations", count=0)]),
            "items": [],
        }
    )
    curriculum, progress = _curriculum_and_progress()
    with pytest.raises(ExportError, match="empty"):
        export_snapshot(
            snapshot,
            tmp_path / "export",
            asset_root=tmp_path,
            curriculum=curriculum,
            current_progress=progress,
            pandoc_executable=tmp_path / "missing-pandoc",
            soffice_executable=tmp_path / "missing-soffice",
        )


def _pandoc_path() -> str:
    path = shutil.which("pandoc")
    if path:
        return path
    pypandoc = pytest.importorskip("pypandoc")
    return pypandoc.get_pandoc_path()


def test_formula_with_injected_math_delimiter_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="delimiter"):
        _pandoc_formula("x$$\n\n$$y", _pandoc_path(), tmp_path, {})


def test_formula_nodes_are_converted_independently_to_omml(tmp_path: Path) -> None:
    pandoc = _pandoc_path()
    document = Document()
    paragraph = document.add_paragraph()
    cache = {}
    _append_nodes(
        paragraph,
        [
            ContentNode(kind="formula", latex="x^2"),
            ContentNode(kind="text", text="，"),
            ContentNode(kind="formula", latex=r"\sqrt{2}"),
        ],
        tmp_path,
        pandoc,
        tmp_path,
        cache,
    )
    assert (
        len(
            list(
                paragraph._p.iter(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"
                )
            )
        )
        == 2
    )
    assert len(cache) == 2


def test_unknown_latex_command_with_pandoc_warning_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="warning|incomplete"):
        _pandoc_formula(r"\totallyunknowncommand{x}", _pandoc_path(), tmp_path, {})


def test_image_asset_path_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"synthetic")

    with pytest.raises(ExportError, match="escapes"):
        _resolve_asset("../outside.png", asset_root)

    link = asset_root / "linked.png"
    link.symlink_to(outside)
    with pytest.raises(ExportError, match="escapes"):
        _resolve_asset("linked.png", asset_root)


def test_remote_image_asset_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="remote"):
        _resolve_asset("https://example.invalid/figure.png", tmp_path)


def test_tall_image_is_scaled_to_printable_page_height(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    image_path = asset_root / "tall.png"
    Image.new("RGB", (100, 2000), "white").save(image_path)
    paragraph = Document().add_paragraph()
    _append_nodes(
        paragraph,
        [ContentNode(kind="image", asset_path="tall.png")],
        asset_root,
        "not-used",
        tmp_path,
        {},
    )
    assert paragraph._p.xpath(".//wp:extent")[0].get("cy")
    assert int(paragraph._p.xpath(".//wp:extent")[0].get("cy")) <= 6 * 914400
