"""Behavioral acceptance cases for offline selection and contract boundaries."""

import itertools
import random

import pytest
from app.contracts import (
    ContentNode,
    Curriculum,
    KnowledgePoint,
    PracticeRequest,
    PracticeSnapshot,
    Question,
    Quota,
    SourceRegion,
    StudentProgress,
)
from app.curriculum import CurriculumIndex
from app.practice import build_practice, validate_current_progress
from pydantic import ValidationError


def curriculum():
    return Curriculum(
        version="demo",
        points=[
            KnowledgePoint(id="A", name="A"),
            KnowledgePoint(id="B", name="B", prerequisite_ids=["A"]),
            KnowledgePoint(id="C", name="C", prerequisite_ids=["B"]),
        ],
    )


def question(id, points=None, **changes):
    data = {
        "id": id,
        "canonical_id": id,
        "content_fingerprint": id,
        "curriculum_version": "demo",
        "stem": [ContentNode(kind="text", text=f"question {id}")],
        "answer": [ContentNode(kind="text", text="answer")],
        "point_ids": points or ["A"],
        "difficulty_score": 30,
        "sources": [SourceRegion(asset_id="demo", page=1, bbox=(0, 0, 1, 1))],
    }
    return Question(**(data | changes))


def progress(points=None):
    return StudentProgress(
        student_id="student",
        revision="1",
        curriculum_version="demo",
        learned_point_ids=points if points is not None else ["A", "B", "C"],
    )


def test_global_assignment_avoids_greedy_false_shortfall():
    result = build_practice(
        [question("shared", ["A", "B"]), question("only-a")],
        curriculum(),
        progress(),
        PracticeRequest(quotas=[Quota(point_id="A", count=1), Quota(point_id="B", count=1)]),
    )
    assert not result.shortfalls
    assert {i.target_point_id: i.question.id for i in result.items} == {
        "A": "only-a",
        "B": "shared",
    }


def test_filters_transitive_prerequisites_and_all_tags():
    questions = [
        question("c", ["C"]),
        question("mixed", ["A", "B"]),
        question("extra", prerequisite_ids=["C"]),
        question("ok"),
    ]
    result = build_practice(
        questions,
        curriculum(),
        progress(["A"]),
        PracticeRequest(quotas=[Quota(point_id="A", count=2)]),
    )
    assert [i.question.id for i in result.items] == ["ok"]
    assert result.shortfalls[0].selected == 1


def test_duplicate_groups_do_not_fill_multiple_slots_and_exclude_original_copies():
    questions = [
        question("v1"),
        question("v2", canonical_id="v1"),
        question("scan", content_fingerprint="v2"),
        question("unique"),
    ]
    # v2 connects canonical v1 and fingerprint v2; scan is in that same identity group.
    result = build_practice(
        questions, curriculum(), progress(), PracticeRequest(quotas=[Quota(point_id="A", count=4)])
    )
    assert len(result.items) == 2
    excluded = build_practice(
        questions,
        curriculum(),
        progress(),
        PracticeRequest(quotas=[Quota(point_id="A", count=4)], excluded_question_ids=["v1"]),
    )
    assert [i.question.id for i in excluded.items] == ["unique"]


def test_difficulty_quota_and_shortfall_are_exact():
    result = build_practice(
        [question("easy"), question("hard", difficulty_score=95)],
        curriculum(),
        progress(),
        PracticeRequest(
            quotas=[Quota(point_id="A", count=3, by_difficulty={"basic": 1, "intermediate": 2})]
        ),
    )
    assert [i.question.id for i in result.items] == ["easy"]
    assert result.shortfalls[0].difficulty == "intermediate"
    assert result.shortfalls[0].requested == 2
    assert result.shortfalls[0].selected == 0


@pytest.mark.parametrize("bad", [True, "1", 1.1, -1])
def test_quota_rejects_noninteger_counts(bad):
    with pytest.raises(ValidationError):
        Quota(point_id="A", count=1, by_difficulty={"basic": bad})


def test_default_is_ten_and_snapshot_independent_of_input_mutations():
    q = question("one")
    request = PracticeRequest(quotas=[Quota(point_id="A")])
    result = build_practice([q], curriculum(), progress(), request)
    assert result.shortfalls[0].requested == 10
    q.stem[0].text = "modified"
    request.quotas[0].count = 5
    assert result.items[0].question.stem[0].text == "question one"
    assert result.request.quotas[0].count == 10


def test_progress_withdrawal_prevents_export_and_different_student_rejected():
    result = build_practice(
        [question("b", ["B"])],
        curriculum(),
        progress(),
        PracticeRequest(quotas=[Quota(point_id="B", count=1)]),
    )
    with pytest.raises(ValueError, match="excludes"):
        validate_current_progress(result, curriculum(), progress(["A"]))
    other = progress().model_copy(update={"student_id": "other"})
    with pytest.raises(ValueError, match="different student"):
        validate_current_progress(result, curriculum(), other)


def test_unknown_id_and_cycle_fail_closed():
    with pytest.raises(ValueError, match="unknown"):
        build_practice(
            [question("bad", ["unknown"])],
            curriculum(),
            progress(),
            PracticeRequest(quotas=[Quota(point_id="A")]),
        )
    cyclic = Curriculum(
        version="demo",
        points=[
            KnowledgePoint(id="A", name="A", prerequisite_ids=["B"]),
            KnowledgePoint(id="B", name="B", prerequisite_ids=["A"]),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        CurriculumIndex(cyclic)


def test_snapshot_rejects_fabricated_shortfall_and_excess_count():
    result = build_practice(
        [question("one"), question("two")],
        curriculum(),
        progress(),
        PracticeRequest(quotas=[Quota(point_id="A", count=2)]),
    )
    data = result.model_dump()
    data["request"]["quotas"][0]["count"] = 1
    with pytest.raises(ValidationError, match="exceeds"):
        PracticeSnapshot.model_validate(data)
    data = result.model_dump()
    data["shortfalls"] = [{"point_id": "A", "requested": 2, "selected": 0}]
    with pytest.raises(ValidationError, match="shortfalls"):
        PracticeSnapshot.model_validate(data)


def test_small_random_cases_match_exhaustive_maximum_allocation():
    rng = random.Random(42)
    c = Curriculum(version="demo", points=[KnowledgePoint(id=p, name=p) for p in ["A", "B"]])
    for _ in range(35):
        qs = [question(str(i), rng.choice([["A"], ["B"], ["A", "B"]])) for i in range(5)]
        counts = {"A": rng.randrange(3), "B": rng.randrange(3)}
        req = PracticeRequest(quotas=[Quota(point_id=p, count=n) for p, n in counts.items()])
        best = 0
        for assignments in itertools.product([None, "A", "B"], repeat=len(qs)):
            if all(p is None or p in q.point_ids for p, q in zip(assignments, qs)) and all(
                assignments.count(p) <= n for p, n in counts.items()
            ):
                best = max(best, sum(p is not None for p in assignments))
        result = build_practice(qs, c, progress(["A", "B"]), req)
        assert len(result.items) == best


def test_selection_is_stable_when_candidate_input_is_reordered():
    qs = [question(str(i)) for i in range(10)]
    req = PracticeRequest(quotas=[Quota(point_id="A", count=3)], seed=99)
    first = build_practice(qs, curriculum(), progress(), req)
    second = build_practice(list(reversed(qs)), curriculum(), progress(), req)
    assert [i.question.id for i in first.items] == [i.question.id for i in second.items]
