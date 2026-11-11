"""Deterministic quota allocation. Never relaxes progress or difficulty constraints."""

from app.allocation import allocate_candidates, identity_groups
from app.contracts import (
    Curriculum,
    PracticeItem,
    PracticeRequest,
    PracticeSnapshot,
    Question,
    StudentProgress,
)
from app.curriculum import CurriculumIndex


def build_practice(
    questions: list[Question],
    curriculum: Curriculum,
    progress: StudentProgress,
    request: PracticeRequest,
    *,
    is_synthetic: bool = True,
) -> PracticeSnapshot:
    index = CurriculumIndex(curriculum)
    if progress.curriculum_version != curriculum.version:
        raise ValueError("student progress curriculum version mismatch")
    index.validate_ids(progress.learned_point_ids)
    if not progress.learned_point_ids:
        raise ValueError("student progress is not set")
    index.validate_ids([q.point_id for q in request.quotas])
    learned = set(progress.learned_point_ids)

    groups = identity_groups(questions, request)
    eligible = set()
    for i, q in enumerate(questions):
        if i not in groups or q.status != "ready":
            continue
        if q.curriculum_version != curriculum.version:
            raise ValueError("question curriculum version mismatch")
        if q.difficulty_version != request.difficulty_version:
            raise ValueError("question difficulty version mismatch")
        required = index.required(q.point_ids + q.prerequisite_ids)
        if required <= learned:
            eligible.add(i)
    allocation = allocate_candidates(questions, request, eligible)
    items = [
        PracticeItem(
            order=order,
            target_point_id=point_id,
            question=questions[i].model_copy(deep=True),
        )
        for order, (i, point_id) in enumerate(allocation.selections, 1)
    ]
    return PracticeSnapshot(
        is_synthetic=is_synthetic,
        student_id=progress.student_id,
        progress_revision=progress.revision,
        curriculum_version=curriculum.version,
        request=request.model_copy(deep=True),
        items=items,
        shortfalls=allocation.shortfalls,
    )


def validate_current_progress(
    snapshot: PracticeSnapshot, curriculum: Curriculum, progress: StudentProgress
) -> None:
    """Export boundary check against CURRENT progress, not just snapshot provenance."""
    if snapshot.student_id != progress.student_id:
        raise ValueError("snapshot belongs to a different student")
    if (
        snapshot.curriculum_version != curriculum.version
        or progress.curriculum_version != curriculum.version
    ):
        raise ValueError("curriculum version mismatch")
    index = CurriculumIndex(curriculum)
    index.validate_ids(progress.learned_point_ids)
    for item in snapshot.items:
        q = item.question
        if not index.required(q.point_ids + q.prerequisite_ids) <= set(progress.learned_point_ids):
            raise ValueError("current student progress excludes selected question")
