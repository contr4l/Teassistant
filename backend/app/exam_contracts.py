"""Evidence-bound contracts for private student exam analysis (FR-07..10)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import Contract

Outcome = Literal["correct", "incorrect", "partial", "unanswered", "unknown"]
Role = Literal["paper", "response", "reference"]
SHA = r"^[0-9a-f]{64}$"
QuestionNumber = Annotated[int, Field(ge=1, le=300, strict=True)]
Coordinate = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]
ExamBox = tuple[Coordinate, Coordinate, Coordinate, Coordinate]
Score = Annotated[float, Field(ge=0, le=10000, strict=True, allow_inf_nan=False)]


class ExamEvidence(Contract):
    page_id: str
    bbox: ExamBox
    kind: Literal["printed_question", "handwriting", "grading", "reference", "header"]
    excerpt: str = ""

    @model_validator(mode="after")
    def bounds(self):
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("evidence needs ordered normalized coordinates")
        return self


class ExamPage(Contract):
    id: str
    document_sha256: str = Field(pattern=SHA)
    source_page: int = Field(ge=1, strict=True)
    role: Role
    image_path: str
    image_sha256: str = Field(pattern=SHA)
    width: int = Field(ge=1, strict=True)
    height: int = Field(ge=1, strict=True)
    parent_page_id: str | None = None
    parent_bbox: ExamBox | None = None

    @model_validator(mode="after")
    def crop_binding(self):
        if (self.parent_page_id is None) != (self.parent_bbox is None):
            raise ValueError("crop needs both parent and transform")
        if self.parent_bbox is not None:
            ExamEvidence(page_id=self.parent_page_id, bbox=self.parent_bbox, kind="header")
        return self


class ExamMaterial(Contract):
    id: str
    sha256: str = Field(pattern=SHA)
    source_path: str
    role: Role
    page_ids: list[str]


class ExamIntake(Contract):
    schema_version: Literal["exam-intake-v1"] = "exam-intake-v1"
    exam_id: str
    student_id: str
    is_synthetic_student: bool = False
    expected_question_numbers: list[QuestionNumber] = Field(min_length=1)
    materials: list[ExamMaterial] = Field(min_length=1)
    pages: list[ExamPage] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self):
        numbers = self.expected_question_numbers
        if len(set(numbers)) != len(numbers) or any(type(n) is not int or n < 1 for n in numbers):
            raise ValueError("expected question numbers must be unique positive integers")
        ids = [p.id for p in self.pages]
        if len(ids) != len(set(ids)):
            raise ValueError("page identifiers must be unique")
        by_id = {page.id: page for page in self.pages}
        physical_keys = [
            (page.document_sha256, page.role, page.source_page)
            for page in self.pages
            if page.parent_page_id is None
        ]
        if len(physical_keys) != len(set(physical_keys)):
            raise ValueError("duplicate physical source page")
        material_ids = [material.id for material in self.materials]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("material identifiers must be unique")
        roots = set()
        for material in self.materials:
            if not material.page_ids or len(material.page_ids) != len(set(material.page_ids)):
                raise ValueError("material needs unique physical pages")
            if not set(material.page_ids) <= set(ids):
                raise ValueError("material refers to missing pages")
            for page_id in material.page_ids:
                page = by_id[page_id]
                if (
                    page.parent_page_id
                    or page.document_sha256 != material.sha256
                    or page.role != material.role
                ):
                    raise ValueError("material/page binding differs")
                roots.add(page_id)
        if roots != {page.id for page in self.pages if page.parent_page_id is None}:
            raise ValueError("unbound physical pages")
        for page in self.pages:
            visited = {page.id}
            current = page
            while current.parent_page_id is not None:
                if current.parent_page_id not in by_id or current.parent_page_id in visited:
                    raise ValueError("invalid or cyclic crop parent")
                parent = by_id[current.parent_page_id]
                if (current.document_sha256, current.source_page, current.role) != (
                    parent.document_sha256,
                    parent.source_page,
                    parent.role,
                ):
                    raise ValueError("crop parent identity differs")
                visited.add(parent.id)
                current = parent
        return self


class ExamRegion(Contract):
    page_id: str
    bbox: ExamBox
    role: Role
    question_numbers: list[QuestionNumber] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def bounds(self):
        ExamEvidence(page_id=self.page_id, bbox=self.bbox, kind="header")
        return self


class PaperQuestion(Contract):
    number: int = Field(ge=1, le=300, strict=True)
    text: str = Field(min_length=1)
    part_paths: list[str] = Field(default_factory=list)
    question_type: Literal["choice", "fill_blank", "free_response", "unknown"] = "unknown"
    max_score: float | None = Field(default=None, ge=0, le=1000, strict=True, allow_inf_nan=False)
    sources: list[ExamEvidence] = Field(min_length=1)
    score_sources: list[ExamEvidence] = Field(default_factory=list)
    completeness: Literal["complete", "partial"] = "complete"
    point_ids: list[str] = Field(default_factory=list)
    difficulty_score: int | None = Field(default=None, ge=0, le=100, strict=True)


class SectionScoreRule(Contract):
    scope: Literal["per_question"] = "per_question"
    literal: str = Field(min_length=1)
    question_numbers: list[QuestionNumber] = Field(min_length=1)
    max_score: float = Field(ge=0, le=1000, strict=True, allow_inf_nan=False)
    sources: list[ExamEvidence] = Field(min_length=1)


class GradingObservation(Contract):
    part_path: str = ""
    kind: Literal["mark", "deducted", "awarded", "maximum", "total_awarded", "total_maximum"]
    value: float | None = Field(default=None, ge=0, le=10000, strict=True, allow_inf_nan=False)
    mark: Literal["correct", "incorrect", "partial", "unclear"] | None = None
    literal: str = Field(min_length=1)
    sources: list[ExamEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def payload(self):
        if self.kind == "mark":
            if self.mark is None or self.value is not None:
                raise ValueError("grading mark needs mark only")
        elif self.value is None or self.mark is not None:
            raise ValueError("numeric grading needs a value only")
        if any(s.kind != "grading" for s in self.sources):
            raise ValueError("grading observations require grading-region evidence")
        return self


class StudentResponse(Contract):
    id: str
    number: int = Field(ge=1, le=300, strict=True)
    part_path: str = ""
    transcript: str = ""
    legibility: Literal["readable", "partial", "illegible", "blank"]
    alternatives: list[str] = Field(default_factory=list)
    sources: list[ExamEvidence] = Field(min_length=1)
    grading: list[GradingObservation] = Field(default_factory=list)
    paper_match: Literal["matched", "uncertain", "conflicting"] = "uncertain"
    association_reason: str = ""
    paper_sources: list[ExamEvidence] = Field(default_factory=list)


class DiagnosisProposal(Contract):
    number: int = Field(ge=1, le=300, strict=True)
    part_path: str = ""
    outcome: Outcome
    point_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    reason_status: Literal["supported", "hypothesis", "unknown"] = "unknown"
    evidence: list[ExamEvidence] = Field(default_factory=list)
    suggested_action: str = ""


class ReferenceAnswer(Contract):
    number: QuestionNumber
    part_path: str = ""
    text: str = Field(min_length=1)
    sources: list[ExamEvidence] = Field(min_length=1)


class TeacherReportItem(Contract):
    title: str = Field(min_length=1, max_length=300)
    question_numbers: list[QuestionNumber] = Field(min_length=1)
    point_ids: list[str] = Field(default_factory=list)
    observation: str = Field(min_length=1, max_length=3000)
    interpretation: str = Field(max_length=3000)
    verification: str = Field(max_length=3000)
    status: Literal["confirmed", "hypothesis", "unknown"]


class TeacherReport(Contract):
    overview: str = Field(min_length=1, max_length=5000)
    strengths: list[TeacherReportItem] = Field(default_factory=list, max_length=10)
    priorities: list[TeacherReportItem] = Field(default_factory=list, max_length=10)
    next_steps: list[str] = Field(default_factory=list, max_length=15)
    unresolved: list[str] = Field(default_factory=list, max_length=30)


class ExamModelResponse(Contract):
    """One stage per request; the orchestrator rejects fields from other stages."""

    task: Literal["layout", "paper", "reference", "response", "diagnosis", "report"]
    exam_title: str | None = None
    page_classification: Literal["paper", "response", "reference", "mixed", "blank", "unclear"] = (
        "unclear"
    )
    printed_page_number: int | None = Field(default=None, ge=1, strict=True)
    printed_page_count: int | None = Field(default=None, ge=1, strict=True)
    regions: list[ExamRegion] = Field(default_factory=list)
    questions: list[PaperQuestion] = Field(default_factory=list)
    scoring_rules: list[SectionScoreRule] = Field(default_factory=list)
    responses: list[StudentResponse] = Field(default_factory=list)
    grading: list[GradingObservation] = Field(default_factory=list)
    diagnoses: list[DiagnosisProposal] = Field(default_factory=list)
    teacher_report: TeacherReport | None = None
    reference_answers: list[ReferenceAnswer] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExamAssociation(Contract):
    number: QuestionNumber
    response_ids: list[str]
    status: Literal["linked", "ambiguous", "unmatched"]
    reason: str
    paper_sources: list[ExamEvidence] = Field(default_factory=list)
    response_sources: list[ExamEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def linked_evidence(self):
        if self.status == "linked" and (
            not self.response_ids or not self.paper_sources or not self.response_sources
        ):
            raise ValueError("linked association needs both sides and response IDs")
        return self


class AnalyzedQuestion(Contract):
    number: QuestionNumber
    outcome: Outcome = "unknown"
    association: ExamAssociation
    response_ids: list[str] = Field(default_factory=list)
    awarded: Score | None = None
    deducted: Score | None = None
    maximum: Score | None = None
    grading_evidence: list[GradingObservation] = Field(default_factory=list)
    point_ids: list[str] = Field(default_factory=list)
    part_diagnoses: list[DiagnosisProposal] = Field(default_factory=list)
    reason: str = ""
    reason_status: Literal["supported", "hypothesis", "unknown"] = "unknown"
    evidence: list[ExamEvidence] = Field(default_factory=list)
    suggested_action: str = ""
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity(self):
        if self.number != self.association.number:
            raise ValueError("question and association number differ")
        return self


class ExamAnalysis(Contract):
    schema_version: Literal["exam-analysis-v1"] = "exam-analysis-v1"
    exam_id: str
    student_id: str
    exam_date: str | None
    curriculum_version: str
    curriculum_sha256: str = Field(pattern=SHA)
    revision: str
    parent_revision: str | None = None
    is_synthetic_student: bool = False
    input_sha256: str = Field(pattern=SHA)
    source_material_sha256: str = Field(pattern=SHA)
    expected_question_numbers: list[QuestionNumber]
    paper_questions: list[PaperQuestion] = Field(default_factory=list)
    student_responses: list[StudentResponse] = Field(default_factory=list)
    reference_answers: list[ReferenceAnswer] = Field(default_factory=list)
    questions: list[AnalyzedQuestion]
    total_awarded: Score | None = None
    total_maximum: Score | None = None
    observed_totals: list[GradingObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    correction_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coverage(self):
        if [question.number for question in self.questions] != self.expected_question_numbers:
            raise ValueError("analysis must preserve all expected questions in order")
        if len(self.expected_question_numbers) != len(set(self.expected_question_numbers)):
            raise ValueError("analysis expected question numbers duplicate")
        return self


class ExamCorrection(Contract):
    id: str
    base_revision: str
    number: QuestionNumber
    part_path: str = ""
    outcome: Outcome | None = None
    response_ids: list[str] | None = None
    response_id: str | None = None
    corrected_transcript: str | None = None
    corrected_response_number: QuestionNumber | None = None
    corrected_part_path: str | None = None
    point_ids: list[str] | None = None
    reason: str | None = None
    reason_status: Literal["supported", "hypothesis", "unknown"] | None = None
    evidence: list[ExamEvidence] = Field(min_length=1)


class LearningPointSummary(Contract):
    point_id: str
    observed_question_count: int
    error_question_count: int
    exam_ids: list[str]
    evidence: list[ExamEvidence]
    interpretation: Literal["practice_signal_only"] = "practice_signal_only"


class LearningSummary(Contract):
    schema_version: Literal["learning-summary-v1"] = "learning-summary-v1"
    student_id: str
    curriculum_version: str
    selected_exam_ids: list[str]
    requested_exam_count: int = 3
    actual_exam_count: int
    points: list[LearningPointSummary]
    unresolved_question_count: int
    warnings: list[str] = Field(default_factory=list)
