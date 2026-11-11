"""Independent local-bank rules; synthetic envelopes are never exported as real papers."""

import hashlib
import json
from dataclasses import dataclass

import pytest
from app.allocation import allocate_candidates
from app.bank_practice_contracts import BankQuestion, PracticeBank
from app.contracts import Curriculum, PracticeRequest, Quota, StudentProgress, difficulty_level
from app.semantic_bank import canonical_hash
from pydantic import ValidationError

RUBRIC = "junior-math-demand-v1"


@dataclass
class Candidate:
    id: str
    point_ids: list[str]
    difficulty_score: int = 50
    revision: str = "synthetic-rev1"
    canonical_id: str = ""
    content_fingerprint: str | None = None

    def __post_init__(self):
        self.canonical_id = self.canonical_id or self.id

    @property
    def difficulty(self):
        return difficulty_level(self.difficulty_score)


def request(*quotas, **kwargs):
    return PracticeRequest(quotas=list(quotas), difficulty_version=RUBRIC, **kwargs)


def curriculum():
    return Curriculum.model_validate(
        {
            "version": "synthetic-bank-v1",
            "points": [
                {"id": "root", "name": "Synthetic root"},
                {"id": "a", "name": "Synthetic A", "prerequisite_ids": ["root"]},
                {"id": "b", "name": "Synthetic B", "prerequisite_ids": ["a"]},
                {"id": "extra", "name": "Synthetic extra", "prerequisite_ids": ["root"]},
            ],
        }
    )


def question(
    qid,
    *,
    points=None,
    prerequisites=None,
    score=50,
    canonical_id=None,
    fingerprint=None,
    status="eligible",
):
    points, prerequisites = points or ["a"], prerequisites or []
    source = {
        "id": qid,
        "number": 1,
        "association_status": "confirmed",
        "structure_status": "imported",
        "answer_available": True,
        "stem_blocks": [{"text": "1. Synthetic question", "html": "<p>1. Synthetic question</p>"}],
        "answer_blocks": [{"text": "Synthetic answer", "html": "<p>Synthetic answer</p>"}],
    }
    assessment = {
        "question_id": qid,
        "status": "assessed",
        "point_ids": points,
        "prerequisite_ids": prerequisites,
        "difficulty_score": score,
        "reasoning": "Synthetic rule test, not a real assessment.",
        "warnings": [],
        "point_evidence": [
            {"point_id": point, "role": role, "reason": "Synthetic evidence."}
            for role, ids in [("target", points), ("prerequisite", prerequisites)]
            for point in ids
        ],
    }
    semantic = {"status": "assessed", "assessment": assessment}
    return BankQuestion(
        id=qid,
        revision=f"synthetic-revision-{qid}",
        canonical_id=canonical_id or qid,
        content_fingerprint=fingerprint or f"synthetic-fingerprint-{qid}",
        curriculum_version=curriculum().version,
        difficulty_version=RUBRIC,
        point_ids=points,
        prerequisite_ids=prerequisites,
        difficulty_score=score,
        status=status,
        block_reasons=[] if status == "eligible" else ["synthetic_block"],
        source_hash=canonical_hash(source),
        semantic_hash=canonical_hash(semantic),
        source_payload=source,
        semantic_payload=semantic,
        documents=[],
        asset_hashes={},
    )


def bank(*questions):
    catalog = curriculum()
    return PracticeBank(
        source_bank_sha256="a" * 64,
        annotations_sha256="b" * 64,
        curriculum_sha256=canonical_hash(catalog.model_dump(mode="json")),
        curriculum_version=catalog.version,
        curriculum_snapshot=catalog,
        questions=list(questions),
    )


def progress(*learned, revision="synthetic-progress-v1", student="synthetic-student"):
    return StudentProgress(
        student_id=student,
        revision=revision,
        curriculum_version=curriculum().version,
        learned_point_ids=list(learned),
    )


def test_global_allocator_fills_overlapping_targets_instead_of_greedy_false_shortfall():
    candidates = [Candidate("flexible", ["a", "b"], 50), Candidate("a-only", ["a"], 80)]
    result = allocate_candidates(
        candidates, request(Quota(point_id="a", count=1), Quota(point_id="b", count=1)), {0, 1}
    )
    assert {(candidates[i].id, point) for i, point in result.selections} == {
        ("a-only", "a"),
        ("flexible", "b"),
    }
    assert result.shortfalls == []


def test_blocked_original_still_excludes_all_transitive_identity_aliases():
    candidates = [
        Candidate("original-blocked", ["a"], canonical_id="same", content_fingerprint="f0"),
        Candidate("alias", ["a"], canonical_id="same", content_fingerprint="f1"),
        Candidate("transitive", ["a"], canonical_id="other", content_fingerprint="f1"),
        Candidate("unique", ["a"], content_fingerprint="f3"),
    ]
    result = allocate_candidates(
        candidates,
        request(Quota(point_id="a", count=3), excluded_question_ids=["original-blocked"]),
        {1, 2, 3},
    )
    assert [(candidates[i].id, point) for i, point in result.selections] == [("unique", "a")]
    assert [(gap.requested, gap.selected) for gap in result.shortfalls] == [(3, 1)]


def test_unknown_fingerprint_is_not_a_shared_identity_in_generic_allocator():
    candidates = [Candidate("one", ["a"]), Candidate("two", ["a"])]
    result = allocate_candidates(candidates, request(Quota(point_id="a", count=2)), {0, 1})
    assert len(result.selections) == 2 and result.shortfalls == []


def test_single_multilabel_question_never_counts_twice():
    result = allocate_candidates(
        [Candidate("one", ["a", "b"])],
        request(Quota(point_id="a", count=1), Quota(point_id="b", count=1)),
        {0},
    )
    assert len(result.selections) == 1
    assert sum(gap.requested - gap.selected for gap in result.shortfalls) == 1


def test_advanced_quota_does_not_substitute_nearby_difficulty():
    candidates = [
        Candidate("basic", ["a"], 39),
        Candidate("middle", ["a"], 69),
        Candidate("advanced", ["a"], 70),
    ]
    req = request(
        Quota(point_id="a", count=4, by_difficulty={"basic": 1, "intermediate": 1, "advanced": 2})
    )
    result = allocate_candidates(candidates, req, {0, 1, 2})
    assert len(result.selections) == 3
    assert [(g.difficulty, g.requested, g.selected) for g in result.shortfalls] == [
        ("advanced", 2, 1)
    ]


@pytest.mark.parametrize(
    "mutation",
    ["semantic_id", "non_boolean_answer", "missing_fingerprint", "semantic_boolean_score"],
)
def test_eligible_envelope_rejects_identity_or_type_corruption(mutation):
    value = question("one", score=1).model_dump()
    if mutation == "semantic_id":
        value["semantic_payload"]["assessment"]["question_id"] = "another-question"
    elif mutation == "non_boolean_answer":
        value["source_payload"]["answer_available"] = 1
    elif mutation == "missing_fingerprint":
        value["content_fingerprint"] = None
    else:
        value["semantic_payload"]["assessment"]["difficulty_score"] = True
    value["source_hash"] = canonical_hash(value["source_payload"])
    value["semantic_hash"] = canonical_hash(value["semantic_payload"])
    with pytest.raises((ValidationError, ValueError)):
        BankQuestion.model_validate(value)


def test_default_ten_returns_actual_gap_without_repeating_three_questions():
    from app.bank_practice import build_bank_practice

    value = build_bank_practice(
        bank(*(question(f"q{n}") for n in range(3))),
        curriculum(),
        progress("root", "a"),
        request(Quota(point_id="a")),
        is_synthetic_student=True,
    )
    assert len(value.items) == 3 and len({x.question.id for x in value.items}) == 3
    assert [(g.requested, g.selected) for g in value.shortfalls] == [(10, 3)]
    assert value.is_synthetic_student is True


@pytest.mark.parametrize("kind", ["other_target", "explicit_prerequisite", "transitive_root"])
def test_every_target_and_prerequisite_must_be_actually_learned(kind):
    from app.bank_practice import build_bank_practice

    if kind == "other_target":
        q, learned = question("q", points=["a", "b"]), ["root", "a"]
    elif kind == "explicit_prerequisite":
        q, learned = question("q", prerequisites=["extra"]), ["root", "a"]
    else:
        q, learned = question("q", points=["b"]), ["a", "b"]
    result = build_bank_practice(
        bank(q),
        curriculum(),
        progress(*learned),
        request(Quota(point_id=q.point_ids[0], count=1)),
        is_synthetic_student=True,
    )
    assert result.items == [] and result.shortfalls[0].selected == 0


def test_missing_progress_and_same_version_curriculum_rewrite_are_rejected():
    from app.bank_practice import build_bank_practice

    original = bank(question("q"))
    req = request(Quota(point_id="a", count=1))
    with pytest.raises(ValueError):
        build_bank_practice(original, curriculum(), progress(), req, is_synthetic_student=True)
    changed = curriculum().model_copy(deep=True)
    changed.points[1].prerequisite_ids = []
    with pytest.raises(ValueError):
        build_bank_practice(original, changed, progress("a"), req, is_synthetic_student=True)


def test_current_progress_can_advance_but_cannot_withdraw_or_change_student():
    from app.bank_practice import build_bank_practice, validate_bank_progress

    snap = build_bank_practice(
        bank(question("q")),
        curriculum(),
        progress("root", "a"),
        request(Quota(point_id="a", count=1)),
        is_synthetic_student=True,
    )
    validate_bank_progress(
        snap, curriculum(), progress("root", "a", "b", revision="synthetic-progress-v2")
    )
    for current in [
        progress("root", revision="withdrawn"),
        progress("root", "a", student="another-student"),
    ]:
        with pytest.raises(ValueError):
            validate_bank_progress(snap, curriculum(), current)


def test_modified_snapshot_cannot_be_exported_as_the_original_bank():
    from app.bank_practice import build_bank_practice, validate_bank_snapshot

    current = bank(question("q"))
    snap = build_bank_practice(
        current,
        curriculum(),
        progress("root", "a"),
        request(Quota(point_id="a", count=1)),
        is_synthetic_student=True,
    )
    snap.items[0].question.source_payload["stem_blocks"][0]["text"] = "Changed synthetic source"
    with pytest.raises(ValueError):
        validate_bank_snapshot(snap, current)


def test_export_rejects_resigned_payload_that_differs_from_raw_bank(tmp_path):
    from app.exporting import ExportError
    from app.source_exporting import _validate_source_bank

    original = bank(question("synthetic-q"))
    raw = json.dumps(
        {"questions": [original.questions[0].source_payload], "documents": []}
    ).encode()
    (tmp_path / "bank.json").write_bytes(raw)
    original.source_bank_sha256 = hashlib.sha256(raw).hexdigest()
    assert _validate_source_bank(tmp_path, original, curriculum()) == {}
    changed = original.model_dump()
    payload = changed["questions"][0]["source_payload"]
    payload["stem_blocks"][0]["text"] = "A different synthetic question"
    payload["stem_blocks"][0]["html"] = "<p>A different synthetic question</p>"
    changed["questions"][0]["source_hash"] = canonical_hash(payload)
    changed["questions"][0]["revision"] = "resigned-synthetic-revision"
    resigned = PracticeBank.model_validate(changed)
    with pytest.raises(ExportError):
        _validate_source_bank(tmp_path, resigned, curriculum())


@pytest.mark.parametrize("mutation", ["assessment_score", "second_row_result_digest"])
def test_semantic_source_call_binds_each_row_even_when_call_is_cached(tmp_path, mutation):
    from copy import deepcopy

    from app.bank_practice import _annotation_reasons
    from app.curriculum import CurriculumIndex

    semantic_dir = tmp_path / "synthetic-semantic"
    semantic_dir.mkdir()
    run_dir = tmp_path / "synthetic-source-run"
    call_dir = run_dir / "calls" / "0001"
    call_dir.mkdir(parents=True)
    questions = [question("synthetic-one"), question("synthetic-two")]
    qids = [q.id for q in questions]
    prompt = b"Synthetic prompt with no real questions."
    request_raw = json.dumps(
        {"question_ids": qids, "prompt_sha256": hashlib.sha256(prompt).hexdigest()}
    ).encode()
    result_raw = json.dumps(
        {
            "question_ids": qids,
            "raw_json_result": {
                "assessments": [q.semantic_payload["assessment"] for q in questions]
            },
        }
    ).encode()
    (call_dir / "prompt.txt").write_bytes(prompt)
    (call_dir / "request.json").write_bytes(request_raw)
    (call_dir / "result.json").write_bytes(result_raw)
    (run_dir / "run.json").write_text(
        json.dumps(
            {"source_bank_sha256": "a" * 64, "difficulty_version": RUBRIC, "question_ids": qids}
        )
    )
    cache = {}
    for q in questions:
        row = deepcopy(q.semantic_payload)
        row.update(
            association_status="confirmed",
            source_hash=q.source_hash,
            validated_catalog_version=curriculum().version,
            validated_catalog_sha256="c" * 64,
            required_point_ids=["a", "root"],
            provenance={
                "source_run_path": str(run_dir),
                "call_id": 1,
                "call_directory": "calls/0001",
                "request_sha256": hashlib.sha256(request_raw).hexdigest(),
                "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
                "result_sha256": hashlib.sha256(result_raw).hexdigest(),
                "response_item_sha256": canonical_hash(row["assessment"]),
            },
        )
        args = (
            q.source_payload,
            row,
            curriculum(),
            "c" * 64,
            CurriculumIndex(curriculum()),
            semantic_dir,
            "a" * 64,
            cache,
        )
        reasons, _ = _annotation_reasons(*args)
        assert reasons == [], "the untouched synthetic call must be valid"
        if q is questions[1]:
            if mutation == "assessment_score":
                row["assessment"]["difficulty_score"] = 99
            else:
                row["provenance"]["result_sha256"] = "0" * 64
            reasons, _ = _annotation_reasons(*args)
            assert reasons, "each sidecar row must bind to its own claimed raw-call evidence"
