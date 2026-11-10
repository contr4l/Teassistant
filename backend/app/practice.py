"""Deterministic quota allocation. Never relaxes progress or difficulty constraints."""

import hashlib

import networkx as nx

from app.contracts import (
    Curriculum,
    PracticeItem,
    PracticeRequest,
    PracticeSnapshot,
    Question,
    Shortfall,
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

    # Connected identity groups: different sources/revisions cannot bypass deduplication.
    identities = nx.Graph()
    for i, q in enumerate(questions):
        node = ("row", i)
        for kind, value in (
            ("id", q.id),
            ("canonical", q.canonical_id),
            ("fingerprint", q.content_fingerprint),
        ):
            identities.add_edge(node, (kind, value))
    groups = {}
    for component in nx.connected_components(identities):
        rows = sorted(n[1] for n in component if n[0] == "row")
        # Excluding an original excludes all copies of the same identity group.
        if any(
            questions[i].id in request.excluded_question_ids
            or questions[i].canonical_id in request.excluded_canonical_ids
            for i in rows
        ):
            continue
        group_key = min(
            (questions[i].canonical_id, questions[i].content_fingerprint, questions[i].id)
            for i in rows
        )
        for i in rows:
            groups[i] = group_key

    eligible = []
    for i, q in enumerate(questions):
        if i not in groups or q.status != "ready":
            continue
        if q.curriculum_version != curriculum.version:
            raise ValueError("question curriculum version mismatch")
        if q.difficulty_version != request.difficulty_version:
            raise ValueError("question difficulty version mismatch")
        required = index.required(q.point_ids + q.prerequisite_ids)
        if required <= learned:
            eligible.append((i, q))
    eligible.sort(key=lambda iq: (iq[1].id, iq[1].revision))

    graph = nx.DiGraph()
    source, sink = ("source",), ("sink",)
    graph.add_nodes_from([source, sink])
    slots = []
    total = sum(q.count for q in request.quotas)
    # Any aggregate tiebreak improvement is smaller than one unit of difficulty distance.
    scale = max(total, 1) * 1000 + 1
    for quota in request.quotas:
        allocation = quota.by_difficulty if quota.by_difficulty is not None else {None: quota.count}
        for level, count in allocation.items():
            slot = ("slot", quota.point_id, level)
            slots.append((slot, quota, level, count))
            graph.add_edge(source, slot, capacity=count, weight=0)
    for i, q in eligible:
        row = ("question", i)
        group = ("group", groups[i])
        graph.add_edge(row, group, capacity=1, weight=0)
        graph.add_edge(group, sink, capacity=1, weight=0)
        for slot, quota, level, _ in slots:
            if quota.point_id not in q.point_ids or (level and q.difficulty != level):
                continue
            tie = (
                int.from_bytes(
                    hashlib.sha256(
                        f"{request.seed}|{quota.point_id}|{q.id}|{q.revision}".encode()
                    ).digest()[:4],
                    "big",
                )
                % 1000
            )
            cost = abs(q.difficulty_score - quota.reference_score) * scale + tie
            graph.add_edge(slot, row, capacity=1, weight=cost)
    flow = nx.max_flow_min_cost(graph, source, sink)
    items, shortfalls = [], []
    for slot, quota, level, count in slots:
        chosen = [questions[n[1]] for n, amount in flow[slot].items() if amount]
        chosen.sort(key=lambda q: (q.difficulty_score, q.id, q.revision))
        for q in chosen:
            # Copy input objects; caller mutations must not change the selected snapshot.
            items.append(
                PracticeItem(
                    order=len(items) + 1,
                    target_point_id=quota.point_id,
                    question=q.model_copy(deep=True),
                )
            )
        if len(chosen) < count:
            shortfalls.append(
                Shortfall(
                    point_id=quota.point_id, difficulty=level, requested=count, selected=len(chosen)
                )
            )
    return PracticeSnapshot(
        is_synthetic=is_synthetic,
        student_id=progress.student_id,
        progress_revision=progress.revision,
        curriculum_version=curriculum.version,
        request=request.model_copy(deep=True),
        items=items,
        shortfalls=shortfalls,
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
