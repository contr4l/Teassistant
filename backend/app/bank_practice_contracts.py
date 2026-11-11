"""Source-native practice contracts. These do not publish legacy Question.ready."""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.contracts import Contract, Curriculum, PracticeRequest, Shortfall, difficulty_level
from app.semantic_bank import canonical_hash
from app.semantic_contracts import SemanticAssessment


class SourceDocument(Contract):
    id: str
    path: str
    sha256: str


class BankQuestion(Contract):
    id: str
    revision: str
    canonical_id: str
    content_fingerprint: str | None
    curriculum_version: str
    difficulty_version: str
    point_ids: list[str]
    prerequisite_ids: list[str]
    difficulty_score: int | None = Field(default=None, ge=0, le=100, strict=True)
    status: Literal["eligible", "blocked"]
    block_reasons: list[str]
    source_hash: str
    semantic_hash: str
    # Complete source-native blocks/OOXML references; no invented pages or boxes.
    source_payload: dict[str, Any]
    semantic_payload: dict[str, Any]
    documents: list[SourceDocument]
    asset_hashes: dict[str, str]

    @model_validator(mode="after")
    def consistency(self):
        if self.source_payload.get("id") != self.id:
            raise ValueError("source question identity differs")
        if canonical_hash(self.source_payload) != self.source_hash:
            raise ValueError("source question snapshot changed")
        if canonical_hash(self.semantic_payload) != self.semantic_hash:
            raise ValueError("semantic snapshot changed")
        if self.status == "eligible":
            if (
                self.block_reasons
                or self.difficulty_score is None
                or not self.point_ids
                or not self.content_fingerprint
            ):
                raise ValueError("eligible question requires complete metadata")
            if self.source_payload.get("association_status") != "confirmed":
                raise ValueError("eligible question requires confirmed association")
            if self.source_payload.get("answer_available") is not True:
                raise ValueError("eligible question requires an answer")
            if self.source_payload.get("structure_status") != "imported":
                raise ValueError("eligible question requires complete source structure")
            assessment = self.semantic_payload.get("assessment") or {}
            # Dict equality treats True as 1; validate the nested contract before comparisons.
            SemanticAssessment.model_validate(assessment)
            if (
                self.semantic_payload.get("status") != "assessed"
                or assessment.get("status") != "assessed"
                or assessment.get("question_id") != self.id
                or assessment.get("warnings")
                or assessment.get("point_ids") != self.point_ids
                or assessment.get("prerequisite_ids") != self.prerequisite_ids
                or assessment.get("difficulty_score") != self.difficulty_score
            ):
                raise ValueError("eligible semantic metadata differs or has warnings")
        elif not self.block_reasons:
            raise ValueError("blocked question requires a reason")
        return self

    @property
    def difficulty(self):
        return (
            difficulty_level(self.difficulty_score) if self.difficulty_score is not None else None
        )


class PracticeBank(Contract):
    schema_version: Literal["source-practice-bank-v1"] = "source-practice-bank-v1"
    mode: Literal["development_validation"] = "development_validation"
    source_bank_sha256: str
    annotations_sha256: str
    curriculum_sha256: str
    curriculum_version: str
    curriculum_snapshot: Curriculum
    questions: list[BankQuestion]

    @model_validator(mode="after")
    def consistency(self):
        if self.curriculum_snapshot.version != self.curriculum_version:
            raise ValueError("practice bank curriculum identity differs")
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("practice bank contains duplicate question IDs")
        return self


class BankPracticeItem(Contract):
    order: int = Field(ge=1, strict=True)
    target_point_id: str
    question: BankQuestion


class BankPracticeSnapshot(Contract):
    schema_version: Literal["source-practice-snapshot-v1"] = "source-practice-snapshot-v1"
    mode: Literal["development_validation"] = "development_validation"
    # True only for an explicitly synthetic student's progress/configuration.
    is_synthetic_student: bool
    student_id: str
    progress_revision: str
    curriculum_version: str
    bank_sha256: str
    request: PracticeRequest
    items: list[BankPracticeItem]
    shortfalls: list[Shortfall]

    @model_validator(mode="after")
    def consistency(self):
        if [item.order for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("practice numbering must be contiguous")
        for values in (
            [item.question.id for item in self.items],
            [item.question.canonical_id for item in self.items],
            [
                item.question.content_fingerprint
                for item in self.items
                if item.question.content_fingerprint
            ],
        ):
            if len(values) != len(set(values)):
                raise ValueError("practice contains duplicate identity")
        quotas = {quota.point_id: quota for quota in self.request.quotas}
        expected = {
            (quota.point_id, level): count
            for quota in self.request.quotas
            for level, count in (quota.by_difficulty or {None: quota.count}).items()
        }
        selected = dict.fromkeys(expected, 0)
        for item in self.items:
            question = item.question
            if (
                question.status != "eligible"
                or question.curriculum_version != self.curriculum_version
                or question.difficulty_version != self.request.difficulty_version
                or item.target_point_id not in question.point_ids
                or item.target_point_id not in quotas
                or question.id in self.request.excluded_question_ids
                or question.canonical_id in self.request.excluded_canonical_ids
            ):
                raise ValueError("practice item violates eligibility or exclusion")
            quota = quotas[item.target_point_id]
            slot = (
                item.target_point_id,
                question.difficulty if quota.by_difficulty is not None else None,
            )
            if slot not in selected:
                raise ValueError("unrequested difficulty")
            selected[slot] += 1
        if any(selected[slot] > count for slot, count in expected.items()):
            raise ValueError("practice exceeds quota")
        missing = {
            slot: (count, selected[slot])
            for slot, count in expected.items()
            if selected[slot] < count
        }
        actual = {
            (item.point_id, item.difficulty): (item.requested, item.selected)
            for item in self.shortfalls
        }
        if len(actual) != len(self.shortfalls) or actual != missing:
            raise ValueError("practice shortfalls differ from actual selection")
        return self
