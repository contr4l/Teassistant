"""Versioned source-bank annotations; never imply business practice readiness."""

from typing import Literal

from pydantic import Field, model_validator

from app.contracts import Contract

PROMPT_VERSION = "source-semantic-prompt-v2"
DIFFICULTY_VERSION = "junior-math-demand-v1"


class PointEvidence(Contract):
    point_id: str = Field(min_length=1)
    role: Literal["target", "prerequisite"]
    reason: str = Field(min_length=1)


class SemanticAssessment(Contract):
    question_id: str = Field(min_length=1)
    status: Literal["assessed", "insufficient_evidence"]
    point_ids: list[str]
    prerequisite_ids: list[str]
    difficulty_score: int | None = Field(ge=0, le=100, strict=True)
    reasoning: str = Field(min_length=1)
    point_evidence: list[PointEvidence]
    warnings: list[str]

    @model_validator(mode="after")
    def consistency(self):
        if self.status == "insufficient_evidence":
            if self.point_ids or self.prerequisite_ids or self.point_evidence:
                raise ValueError("insufficient evidence cannot claim validated labels")
            if self.difficulty_score is not None:
                raise ValueError("insufficient evidence cannot claim a score")
            return self
        if not self.point_ids or self.difficulty_score is None:
            raise ValueError("assessment requires targets and score")
        if len(set(self.point_ids)) != len(self.point_ids) or len(
            set(self.prerequisite_ids)
        ) != len(self.prerequisite_ids):
            raise ValueError("duplicate curriculum identifiers")
        if set(self.point_ids) & set(self.prerequisite_ids):
            raise ValueError("target and prerequisite roles must be distinct")
        expected = {(point, "target") for point in self.point_ids} | {
            (point, "prerequisite") for point in self.prerequisite_ids
        }
        actual = [(item.point_id, item.role) for item in self.point_evidence]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("every label needs exactly one role-specific evidence entry")
        return self


class SemanticBatchResponse(Contract):
    assessments: list[SemanticAssessment] = Field(min_length=1)
