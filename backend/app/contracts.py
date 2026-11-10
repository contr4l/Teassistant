"""Versioned P0 contracts. Structural validity is not proof of recognition correctness."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Difficulty = Literal["basic", "intermediate", "advanced"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)


class ContentNode(Contract):
    kind: Literal["text", "formula", "image"]
    text: str | None = None
    latex: str | None = None
    asset_path: str | None = None

    @model_validator(mode="after")
    def content_matches_kind(self):
        values = {"text": self.text, "formula": self.latex, "image": self.asset_path}
        if not values[self.kind] or any(v is not None for k, v in values.items() if k != self.kind):
            raise ValueError("content node must contain exactly its kind's nonempty payload")
        return self


class SourceRegion(Contract):
    asset_id: Identifier
    page: int = Field(ge=1, strict=True)
    # Coordinates in [0,1] after page normalization: x0,y0,x1,y1.
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def ordered_bbox(self):
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("bbox must be ordered normalized coordinates")
        return self


class Subquestion(Contract):
    id: Identifier
    stem: list[ContentNode] = Field(min_length=1)
    answer: list[ContentNode] = Field(min_length=1)


class Question(Contract):
    id: Identifier
    revision: Identifier = "1"
    canonical_id: Identifier
    content_fingerprint: Identifier
    curriculum_version: Identifier
    stem: list[ContentNode] = Field(min_length=1)
    answer: list[ContentNode] = Field(default_factory=list)
    solution: list[ContentNode] = Field(default_factory=list)
    subquestions: list[Subquestion] = Field(default_factory=list)
    point_ids: list[Identifier] = Field(min_length=1)
    prerequisite_ids: list[Identifier] = Field(default_factory=list)
    difficulty_score: int = Field(ge=0, le=100, strict=True)
    difficulty_version: Identifier = "p0-draft-v1"
    model_version: Identifier = "synthetic"
    status: Literal["ready", "processing_failed"] = "ready"
    sources: list[SourceRegion] = Field(min_length=1)

    @model_validator(mode="after")
    def question_consistency(self):
        for values in (self.point_ids, self.prerequisite_ids, [s.id for s in self.subquestions]):
            if len(values) != len(set(values)):
                raise ValueError("duplicate point or subquestion identifiers")
        if self.status == "ready" and not self.answer and not self.subquestions:
            raise ValueError("ready question needs an answer")
        return self

    @property
    def difficulty(self) -> Difficulty:
        return difficulty_level(self.difficulty_score)


def difficulty_level(score: int) -> Difficulty:
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError("difficulty score must be an integer in [0,100]")
    return "basic" if score < 40 else "intermediate" if score < 70 else "advanced"


class KnowledgePoint(Contract):
    id: Identifier
    name: Identifier
    prerequisite_ids: list[Identifier] = Field(default_factory=list)


class Curriculum(Contract):
    version: Identifier
    is_synthetic: bool = True
    points: list[KnowledgePoint] = Field(min_length=1)


class StudentProgress(Contract):
    student_id: Identifier
    revision: Identifier
    curriculum_version: Identifier
    learned_point_ids: list[Identifier]


class Quota(Contract):
    point_id: Identifier
    count: int = Field(default=10, ge=0, le=100, strict=True)
    by_difficulty: dict[Difficulty, Annotated[int, Field(ge=0, strict=True)]] | None = None
    reference_score: int = Field(default=50, ge=0, le=100, strict=True)

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.by_difficulty is not None:
            if any(type(v) is not int or v < 0 for v in self.by_difficulty.values()):
                raise ValueError("difficulty counts must be nonnegative integers")
            if sum(self.by_difficulty.values()) != self.count:
                raise ValueError("difficulty counts must sum to quota count")
        return self


class PracticeRequest(Contract):
    quotas: list[Quota] = Field(min_length=1, max_length=100)
    excluded_question_ids: list[Identifier] = Field(default_factory=list)
    excluded_canonical_ids: list[Identifier] = Field(default_factory=list)
    seed: int = Field(default=0, strict=True)
    difficulty_version: Identifier = "p0-draft-v1"

    @model_validator(mode="after")
    def unique_targets(self):
        targets = [q.point_id for q in self.quotas]
        if len(targets) != len(set(targets)):
            raise ValueError("each point must have a single quota")
        return self


class PracticeItem(Contract):
    order: int = Field(ge=1, strict=True)
    target_point_id: Identifier
    question: Question


class Shortfall(Contract):
    point_id: Identifier
    difficulty: Difficulty | None = None
    requested: int = Field(ge=0, strict=True)
    selected: int = Field(ge=0, strict=True)


class PracticeSnapshot(Contract):
    schema_version: Literal["p0-v1"] = "p0-v1"
    is_synthetic: bool = True
    student_id: Identifier
    progress_revision: Identifier
    curriculum_version: Identifier
    request: PracticeRequest
    items: list[PracticeItem]
    shortfalls: list[Shortfall] = Field(default_factory=list)

    @model_validator(mode="after")
    def item_consistency(self):
        if [i.order for i in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("item orders must be contiguous from one")
        for values in (
            [i.question.id for i in self.items],
            [i.question.canonical_id for i in self.items],
            [i.question.content_fingerprint for i in self.items],
        ):
            if len(values) != len(set(values)):
                raise ValueError("practice contains duplicate questions")
        targets = {q.point_id for q in self.request.quotas}
        if any(
            i.target_point_id not in targets
            or i.target_point_id not in i.question.point_ids
            or i.question.curriculum_version != self.curriculum_version
            for i in self.items
        ):
            raise ValueError("item target or curriculum mismatch")
        if any(
            i.question.status != "ready"
            or i.question.difficulty_version != self.request.difficulty_version
            for i in self.items
        ):
            raise ValueError("practice requires ready questions with matching difficulty version")
        if any(
            i.question.id in self.request.excluded_question_ids
            or i.question.canonical_id in self.request.excluded_canonical_ids
            for i in self.items
        ):
            raise ValueError("practice contains excluded original questions")
        expected_slots = {}
        for quota in self.request.quotas:
            for level, count in (quota.by_difficulty or {None: quota.count}).items():
                expected_slots[(quota.point_id, level)] = count
        counts = dict.fromkeys(expected_slots, 0)
        for item in self.items:
            quota = next(q for q in self.request.quotas if q.point_id == item.target_point_id)
            slot = (
                item.target_point_id,
                item.question.difficulty if quota.by_difficulty is not None else None,
            )
            if slot not in counts:
                raise ValueError("question difficulty is not requested")
            counts[slot] += 1
        if any(counts[s] > n for s, n in expected_slots.items()):
            raise ValueError("practice exceeds quota")
        missing = {s: (n, counts[s]) for s, n in expected_slots.items() if counts[s] < n}
        actual = {(s.point_id, s.difficulty): (s.requested, s.selected) for s in self.shortfalls}
        if len(actual) != len(self.shortfalls) or actual != missing:
            raise ValueError("shortfalls do not match selected questions")
        return self
