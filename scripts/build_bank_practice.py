"""Local source-bank practice pipeline. No model calls or synthetic question generation."""

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bank_practice import build_bank_practice, load_practice_bank  # noqa: E402
from app.contracts import Curriculum, PracticeRequest, StudentProgress  # noqa: E402
from app.curriculum import CurriculumIndex  # noqa: E402
from app.semantic_bank import canonical_hash  # noqa: E402


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inventory(bank, curriculum, progress=None) -> dict:
    index = CurriculumIndex(curriculum)
    if progress is not None:
        if progress.curriculum_version != curriculum.version:
            raise ValueError("student progress curriculum version mismatch")
        index.validate_ids(progress.learned_point_ids)
    eligible = [question for question in bank.questions if question.status == "eligible"]
    in_progress = [
        question
        for question in eligible
        if progress is None
        or index.required(question.point_ids + question.prerequisite_ids)
        <= set(progress.learned_point_ids)
    ]
    rows = []
    for point in curriculum.points:
        questions = [question for question in in_progress if point.id in question.point_ids]
        levels = Counter(question.difficulty for question in questions)
        rows.append(
            {
                "point_id": point.id,
                "name": point.name,
                "candidate_count": len(questions),
                "by_difficulty": dict(levels),
            }
        )
    return {
        "mode": bank.mode,
        "denominator": len(bank.questions),
        "eligible_before_progress": len(eligible),
        "eligible_within_progress": len(in_progress),
        "blocked": len(bank.questions) - len(eligible),
        "blocked_reason_counts": dict(
            Counter(reason for question in bank.questions for reason in question.block_reasons)
        ),
        "bank_sha256": canonical_hash(bank.model_dump(mode="json")),
        "curriculum_version": curriculum.version,
        "inventory_counts_overlap": True,
        "note": "各考点含多标签重叠，数量不能相加视为可同时选出的总量；实际以容量分配为准。",
        "points": rows,
        "blocked_questions": [
            {"id": question.id, "reasons": question.block_reasons}
            for question in bank.questions
            if question.status == "blocked"
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, required=True, help="source bank directory")
    parser.add_argument("--semantic", type=Path, required=True, help="final semantic directory")
    parser.add_argument("--progress", type=Path, help="current StudentProgress JSON")
    parser.add_argument("--request", type=Path, help="PracticeRequest JSON")
    parser.add_argument("--output", type=Path, required=True, help="new private output directory")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument(
        "--synthetic-student",
        action="store_true",
        help="explicitly label demonstration student/progress as synthetic",
    )
    parser.add_argument("--export", action="store_true", help="require all four DOCX/PDF outputs")
    parser.add_argument("--soffice", type=Path, help="explicit LibreOffice executable")
    parser.add_argument(
        "--layout",
        choices=["answer_space", "compact"],
        default="answer_space",
        help="student print layout; teacher export contains answers and solutions only",
    )
    args = parser.parse_args()
    if not args.inventory_only and (args.progress is None or args.request is None):
        parser.error("practice requires --progress and --request")
    if args.inventory_only and args.export:
        parser.error("--inventory-only cannot be combined with --export")
    output = args.output.absolute()
    resolved_output = output.resolve()
    for protected in (args.bank.resolve(), args.semantic.resolve()):
        if (
            resolved_output == protected
            or resolved_output.is_relative_to(protected)
            or protected.is_relative_to(resolved_output)
        ):
            parser.error("output must be separate from source bank and semantic input directories")
    if output.exists():
        parser.error("output already exists; use a fresh directory to preserve existing snapshots")
    bank = load_practice_bank(args.bank, args.semantic)
    curriculum = Curriculum.model_validate_json((args.semantic / "curriculum.json").read_bytes())
    progress = (
        StudentProgress.model_validate_json(args.progress.read_bytes()) if args.progress else None
    )
    summary = inventory(bank, curriculum, progress)
    snapshot = None
    if not args.inventory_only:
        request = PracticeRequest.model_validate_json(args.request.read_bytes())
        snapshot = build_bank_practice(
            bank, curriculum, progress, request, is_synthetic_student=args.synthetic_student
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        write_json(stage / "inventory.json", summary)
        if progress is not None:
            write_json(stage / "progress.json", progress.model_dump(mode="json"))
        if snapshot is not None:
            write_json(stage / "snapshot.json", snapshot.model_dump(mode="json"))
            summary.update(
                selected=len(snapshot.items),
                shortfalls=[item.model_dump(mode="json") for item in snapshot.shortfalls],
                is_synthetic_student=snapshot.is_synthetic_student,
            )
            if args.export:
                from app.source_exporting import export_bank_snapshot

                # Rebind to current inputs at export, rather than trusting the old in-memory copies.
                current = StudentProgress.model_validate_json(args.progress.read_bytes())
                current_bank = load_practice_bank(args.bank, args.semantic)
                current_curriculum = Curriculum.model_validate_json(
                    (args.semantic / "curriculum.json").read_bytes()
                )
                result = export_bank_snapshot(
                    snapshot,
                    stage / "exports",
                    bank_dir=args.bank,
                    practice_bank=current_bank,
                    curriculum=current_curriculum,
                    current_progress=current,
                    soffice_executable=args.soffice,
                    layout=args.layout,
                )

                # Exporter paths point to the stage; publish the CLI manifest with final paths.
                def published(value):
                    if isinstance(value, dict):
                        return {key: published(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [published(item) for item in value]
                    if isinstance(value, str) and value.startswith(str(stage) + os.sep):
                        return str(output / Path(value).relative_to(stage))
                    return value

                result = published(result)
                write_json(stage / "export-result.json", result)
                summary["exports"] = result["files"]
        write_json(stage / "summary.json", summary)
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in {"points", "blocked_questions"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Saved private evidence: {output}")


if __name__ == "__main__":
    main()
