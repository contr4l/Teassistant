"""Offline deterministic selection demonstration, explicitly synthetic."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts import (  # noqa: E402
    ContentNode,
    Curriculum,
    KnowledgePoint,
    PracticeRequest,
    Question,
    Quota,
    SourceRegion,
    StudentProgress,
)
from app.practice import build_practice  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/selection-snapshot.json")
    args = parser.parse_args()
    curriculum = Curriculum(
        version="synthetic-v1",
        points=[
            KnowledgePoint(id="linear", name="一元一次方程"),
            KnowledgePoint(id="system", name="二元一次方程组", prerequisite_ids=["linear"]),
            KnowledgePoint(id="quadratic", name="一元二次方程", prerequisite_ids=["linear"]),
        ],
    )
    progress = StudentProgress(
        student_id="synthetic-student",
        revision="demo-1",
        curriculum_version=curriculum.version,
        learned_point_ids=["linear", "system"],
    )
    questions = []
    for i in range(12):
        questions.append(
            Question(
                id=f"demo-{i}",
                canonical_id=f"demo-{i}",
                content_fingerprint=f"synthetic-{i}",
                curriculum_version=curriculum.version,
                stem=[ContentNode(kind="text", text=f"解方程：x+{i}={i + 3}。")],
                answer=[ContentNode(kind="text", text="x=3")],
                point_ids=["linear"],
                difficulty_score=20 + i,
                sources=[SourceRegion(asset_id="synthetic-source", page=1, bbox=(0, 0, 1, 1))],
            )
        )
    snapshot = build_practice(
        questions, curriculum, progress, PracticeRequest(quotas=[Quota(point_id="linear")], seed=42)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"SYNTHETIC: selected={len(snapshot.items)}, shortfalls={len(snapshot.shortfalls)}")
    print(args.output)


if __name__ == "__main__":
    main()
