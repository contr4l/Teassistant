#!/usr/bin/env python3
"""Generate a synthetic math practice snapshot and export its four files."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.contracts import (  # noqa: E402
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
from app.exporting import export_snapshot  # noqa: E402


def _make_geometry(path: Path) -> None:
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    label_font = ImageFont.load_default(size=28)
    a, b, c = (175, 410), (785, 410), (455, 90)
    draw.line((a, b, c, a), fill="#263a62", width=8)
    draw.line((455, 90, 455, 410), fill="#cb4b32", width=5)
    draw.text((145, 418), "A", fill="#172033", font=label_font)
    draw.text((795, 418), "B", fill="#172033", font=label_font)
    draw.text((450, 58), "C", fill="#172033", font=label_font)
    draw.text((465, 235), "h", fill="#cb4b32", font=label_font)
    image.save(path, format="PNG")


def _make_snapshot() -> PracticeSnapshot:
    question = Question(
        id="demo-q-001",
        canonical_id="demo-canonical-001",
        content_fingerprint="synthetic-fingerprint-001",
        curriculum_version="demo-curriculum-v1",
        stem=[
            ContentNode(kind="text", text="解方程，并完成下列问题。"),
            ContentNode(kind="formula", latex=r"\frac{x+1}{2}=\sqrt{9}"),
        ],
        answer=[ContentNode(kind="text", text="方程的解为 x=5。")],
        solution=[
            ContentNode(kind="text", text="先化简根式，再移项："),
            ContentNode(kind="formula", latex=r"\sqrt{9}=3,\quad x=5"),
        ],
        subquestions=[
            Subquestion(
                id="demo-q-001-a",
                stem=[
                    ContentNode(kind="text", text="如图，若底边长为 8，高为 3，求三角形面积。"),
                    ContentNode(kind="image", asset_path="geometry.png"),
                ],
                answer=[ContentNode(kind="formula", latex=r"S=\frac{1}{2}\times 8\times 3=12")],
            ),
            Subquestion(
                id="demo-q-001-b",
                stem=[
                    ContentNode(kind="text", text="解下列方程组："),
                    ContentNode(kind="formula", latex=r"\begin{cases}x+y=5\\x-y=1\end{cases}"),
                ],
                answer=[ContentNode(kind="formula", latex=r"x=3,\quad y=2")],
            ),
            Subquestion(
                id="demo-q-001-c",
                stem=[
                    ContentNode(kind="text", text="已知 a₁ 为正数，且："),
                    ContentNode(kind="formula", latex=r"a_1^2=9"),
                    ContentNode(kind="text", text="求 a₁。"),
                ],
                answer=[ContentNode(kind="formula", latex=r"a_1=3")],
            ),
        ],
        point_ids=["equations-and-geometry"],
        difficulty_score=50,
        sources=[SourceRegion(asset_id="synthetic-source", page=1, bbox=(0.05, 0.05, 0.95, 0.95))],
    )
    snapshot = PracticeSnapshot(
        is_synthetic=True,
        student_id="synthetic-student",
        progress_revision="demo-progress-v1",
        curriculum_version="demo-curriculum-v1",
        request=PracticeRequest(quotas=[Quota(point_id="equations-and-geometry", count=1)]),
        items=[PracticeItem(order=1, target_point_id="equations-and-geometry", question=question)],
    )
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "work" / "export-demo")
    parser.add_argument(
        "--pandoc", default=os.environ.get("TEASSISTANT_PANDOC") or shutil.which("pandoc")
    )
    parser.add_argument(
        "--soffice", default=os.environ.get("TEASSISTANT_SOFFICE") or shutil.which("soffice")
    )
    args = parser.parse_args()
    if not args.pandoc:
        parser.error("Pandoc is required; pass --pandoc or set TEASSISTANT_PANDOC")
    if not args.soffice:
        parser.error("LibreOffice soffice is required; pass --soffice or set TEASSISTANT_SOFFICE")

    curriculum = Curriculum(
        version="demo-curriculum-v1",
        points=[KnowledgePoint(id="equations-and-geometry", name="方程与几何")],
    )
    current_progress = StudentProgress(
        student_id="synthetic-student",
        revision="demo-current-progress-v1",
        curriculum_version="demo-curriculum-v1",
        learned_point_ids=["equations-and-geometry"],
    )
    with tempfile.TemporaryDirectory(prefix="teassistant-export-demo-assets-") as temp:
        asset_root = Path(temp)
        _make_geometry(asset_root / "geometry.png")
        result = export_snapshot(
            _make_snapshot(),
            args.output,
            asset_root=asset_root,
            curriculum=curriculum,
            current_progress=current_progress,
            pandoc_executable=args.pandoc,
            soffice_executable=args.soffice,
        )
    for label, path in (
        ("学生 DOCX", result.student_docx),
        ("学生 PDF", result.student_pdf),
        ("教师 DOCX", result.teacher_docx),
        ("教师 PDF", result.teacher_pdf),
    ):
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
