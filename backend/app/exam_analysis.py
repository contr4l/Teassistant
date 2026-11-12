"""Deterministic, evidence-bound analysis for one student's exam."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from typing import Any

from app.contracts import Curriculum
from app.curriculum import CurriculumIndex
from app.exam_contracts import (
    AnalyzedQuestion,
    DiagnosisProposal,
    ExamAnalysis,
    ExamAssociation,
    ExamCorrection,
    ExamEvidence,
    ExamIntake,
    ExamModelResponse,
    ExamPage,
    GradingObservation,
    LearningPointSummary,
    LearningSummary,
    PaperQuestion,
    ReferenceAnswer,
    SectionScoreRule,
    StudentResponse,
)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_dump(item) for item in value]
    return value


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(_dump(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(value: Any, model):
    if isinstance(value, model):
        value = value.model_dump(mode="python")
    return model.model_validate(value)


def _stage_results(values: list[Any], task: str) -> list[ExamModelResponse]:
    if not isinstance(values, list):
        raise ValueError(f"{task} results must be a list")
    results = [_normalize(value, ExamModelResponse) for value in values]
    if any(result.task != task for result in results):
        raise ValueError(f"expected only {task} model results")
    return results


def _canonical_part_maps(
    questions: list[PaperQuestion], warnings: list[str]
) -> dict[int, dict[str, str]]:
    maps: dict[int, dict[str, str]] = {}
    for question in questions:
        paths = question.part_paths
        prefix = f"{question.number}/"
        if not paths or not all(path.startswith(prefix) for path in paths):
            continue
        suffixes = [path[len(prefix) :] for path in paths]
        labels = re.findall(r"[（(]([1-9][0-9]?)[）)]", question.text)
        if (
            any(not suffix.isdigit() for suffix in suffixes)
            or len(set(suffixes)) != len(suffixes)
            or len(set(labels)) != len(labels)
            or set(suffixes) != set(labels)
        ):
            warnings.append(
                f"question {question.number}: part paths kept verbatim because printed labels do not prove a safe prefix removal"
            )
            continue
        mapping = {path: path[len(prefix) :] for path in paths}
        maps[question.number] = mapping
        warnings.append(
            f"question {question.number}: canonicalized derived part paths from question-prefixed labels"
        )
    return maps


def _map_part_path(number: int, path: str, maps: dict[int, dict[str, str]]) -> str:
    return maps.get(number, {}).get(path, path)


def _evidence_ok(
    evidence: ExamEvidence,
    pages: dict[str, ExamPage],
    allowed: dict[str, set[str]],
) -> bool:
    page = pages.get(evidence.page_id)
    return page is not None and page.role in allowed and evidence.kind in allowed[page.role]


def _valid_evidence_list(
    evidence: list[ExamEvidence], pages: dict[str, ExamPage], allowed: dict[str, set[str]]
) -> bool:
    return bool(evidence) and all(_evidence_ok(item, pages, allowed) for item in evidence)


def _adjacent_header_quote(
    quote: str, evidence: list[ExamEvidence], pages: dict[str, ExamPage]
) -> bool:
    header_fragments = [
        item
        for item in evidence
        if item.kind == "header"
        and item.excerpt.strip()
        and _evidence_ok(item, pages, _PAPER_SCORE_EVIDENCE)
    ]
    if any(quote in item.excerpt for item in header_fragments):
        return True
    if len(header_fragments) < 2 or len(header_fragments) != len(evidence):
        return False
    positioned = []
    for item in header_fragments:
        region = _root_region(item, pages)
        if region is None:
            return False
        page_id, bbox = region
        positioned.append((page_id, bbox, item.excerpt))
    page_ids = {item[0] for item in positioned}
    if len(page_ids) != 1:
        return False
    positioned.sort(key=lambda item: (item[1][1], item[1][0]))
    for (_, previous, _), (_, current, _) in zip(positioned, positioned[1:], strict=False):
        px0, py0, px1, py1 = previous
        cx0, cy0, cx1, cy1 = current
        vertical_overlap = min(py1, cy1) - max(py0, cy0)
        horizontal_gap = cx0 - px1
        same_line_adjacent = vertical_overlap > 0 and -0.015 <= horizontal_gap <= 0.06
        line_wrap_adjacent = -0.005 <= cy0 - py1 <= 0.03 and cx0 <= px0 + 0.18
        if not (same_line_adjacent or line_wrap_adjacent):
            return False

    def compact(value: str) -> str:
        return re.sub(r"\s+", "", value)

    return compact("".join(item[2] for item in positioned)) == compact(quote)


_PAPER_EVIDENCE = {"paper": {"printed_question"}}
_PAPER_SCORE_EVIDENCE = {"paper": {"printed_question", "header"}}
_RESPONSE_EVIDENCE = {"response": {"handwriting", "grading"}}
_GRADING_EVIDENCE = {"response": {"grading"}}
_MAXIMUM_EVIDENCE = {"response": {"grading"}, "paper": {"grading"}}
_TOTAL_EVIDENCE = {"response": {"grading"}, "paper": {"grading"}}
_DIAGNOSIS_EVIDENCE = {
    "paper": {"printed_question"},
    "response": {"handwriting", "grading"},
    "reference": {"reference"},
}


def _unique_evidence(items: list[ExamEvidence]) -> list[ExamEvidence]:
    seen: set[str] = set()
    output = []
    for item in items:
        key = _canonical_sha(item)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _valid_ids(ids: list[str], curriculum_ids: set[str]) -> tuple[list[str], bool]:
    invalid = any(point_id not in curriculum_ids for point_id in ids)
    return list(dict.fromkeys(point_id for point_id in ids if point_id in curriculum_ids)), invalid


def _question_parts_compatible(question: PaperQuestion, responses: list[StudentResponse]) -> bool:
    if len(responses) <= 1:
        if not responses:
            return False
        return (
            not responses[0].part_path
            or not question.part_paths
            or responses[0].part_path in question.part_paths
        )
    paths = [response.part_path for response in responses]
    if any(not path for path in paths) or len(paths) != len(set(paths)):
        return False
    if question.part_paths and not set(paths) <= set(question.part_paths):
        return False
    return True


def _source_page_ids(items: list[ExamEvidence]) -> set[str]:
    return {item.page_id for item in items}


def _root_region(evidence: ExamEvidence, pages: dict[str, ExamPage]):
    page = pages.get(evidence.page_id)
    if page is None:
        return None
    x0, y0, x1, y1 = evidence.bbox
    visited: set[str] = set()
    while page.parent_page_id is not None:
        if page.id in visited:
            return None
        visited.add(page.id)
        parent = pages.get(page.parent_page_id)
        if parent is None or page.parent_bbox is None:
            return None
        px0, py0, px1, py1 = page.parent_bbox
        x0, x1 = px0 + x0 * (px1 - px0), px0 + x1 * (px1 - px0)
        y0, y1 = py0 + y0 * (py1 - py0), py0 + y1 * (py1 - py0)
        page = parent
    return page.id, (x0, y0, x1, y1)


def _paper_response_overlap(
    paper_evidence: list[ExamEvidence],
    response_evidence: list[ExamEvidence],
    pages: dict[str, ExamPage],
) -> bool:
    paper_regions = [_root_region(item, pages) for item in paper_evidence]
    response_regions = [_root_region(item, pages) for item in response_evidence]
    for paper_region in paper_regions:
        if paper_region is None:
            continue
        paper_page, (px0, py0, px1, py1) = paper_region
        for response_region in response_regions:
            if response_region is None or response_region[0] != paper_page:
                continue
            rx0, ry0, rx1, ry1 = response_region[1]
            if min(px1, rx1) > max(px0, rx0) and min(py1, ry1) > max(py0, ry0):
                return True
    return False


def _score_number(value: float | None) -> float | None:
    return None if value is None else float(value)


def _single_observed_value(
    observations: list[GradingObservation], kind: str, warnings: list[str], label: str
) -> float | None:
    values = sorted({float(item.value) for item in observations if item.kind == kind})
    if len(values) > 1:
        warnings.append(f"conflicting {label} observations; value left unresolved")
        return None
    return values[0] if values else None


def _dedupe_observations(items: list[GradingObservation]) -> list[GradingObservation]:
    by_hash = {_canonical_sha(item): item for item in items}
    return [by_hash[key] for key in sorted(by_hash)]


def _root_outcome(outcomes: list[str]) -> str:
    if not outcomes or any(value == "unknown" for value in outcomes):
        return "unknown"
    if all(value == "correct" for value in outcomes):
        return "correct"
    if all(value == "unanswered" for value in outcomes):
        return "unanswered"
    if "incorrect" in outcomes:
        return "incorrect"
    if "partial" in outcomes:
        return "partial"
    return "unknown"


def _analysis_revision(payload: dict[str, Any]) -> str:
    without_revision = {key: value for key, value in payload.items() if key != "revision"}
    return _canonical_sha(without_revision)


def _associate_question(
    number: int,
    paper_records: list[PaperQuestion],
    response_records: list[StudentResponse],
    pages: dict[str, ExamPage],
    *,
    selected_ids: list[str] | None = None,
) -> tuple[ExamAssociation, PaperQuestion | None, list[StudentResponse]]:
    candidates = [question for question in paper_records if question.number == number]
    paper = candidates[0] if len(candidates) == 1 else None
    responses = [response for response in response_records if response.number == number]
    paper_sources = _unique_evidence(
        paper.sources if paper else [source for item in candidates for source in item.sources]
    )
    response_sources = _unique_evidence(
        [source for response in responses for source in response.sources]
    )
    response_id_counts: dict[str, int] = defaultdict(int)
    for response in response_records:
        response_id_counts[response.id] += 1
    ids = list(dict.fromkeys(response.id for response in responses))
    if len(candidates) > 1:
        return (
            ExamAssociation(
                number=number,
                response_ids=ids,
                status="ambiguous",
                reason="duplicate printed-question records prevent a unique association",
                paper_sources=paper_sources,
                response_sources=response_sources,
            ),
            None,
            [],
        )
    if paper is None or not responses:
        return (
            ExamAssociation(
                number=number,
                response_ids=ids,
                status="unmatched",
                reason="no validated paper question and student response are linked",
                paper_sources=paper_sources,
                response_sources=response_sources,
            ),
            paper,
            [],
        )
    if selected_ids is not None and not selected_ids:
        return (
            ExamAssociation(
                number=number,
                response_ids=[],
                status="unmatched",
                reason="teacher marked no response as linked",
                paper_sources=paper_sources,
                response_sources=response_sources,
            ),
            paper,
            [],
        )
    selected = responses
    if selected_ids is not None:
        selected = [response for response in responses if response.id in set(selected_ids)]
    valid = [
        response
        for response in selected
        if response_id_counts[response.id] == 1
        and response.paper_match == "matched"
        and response.paper_sources
        and _paper_response_overlap(paper.sources, response.paper_sources, pages)
    ]
    linked = (
        bool(valid) and len(valid) == len(selected) and _question_parts_compatible(paper, valid)
    )
    return (
        ExamAssociation(
            number=number,
            response_ids=[response.id for response in valid] if linked else ids,
            status="linked" if linked else "ambiguous",
            reason=(
                "question number, part path, and overlapping source regions agree"
                if linked
                else "one or more response-to-paper bindings remain uncertain"
            ),
            paper_sources=paper_sources,
            response_sources=_unique_evidence(
                [
                    source
                    for response in (valid if linked else responses)
                    for source in response.sources
                ]
            ),
        ),
        paper,
        valid if linked else [],
    )


def analyze_exam(
    *,
    intake: ExamIntake,
    paper_results: list[ExamModelResponse],
    response_results: list[ExamModelResponse],
    diagnosis_results: list[ExamModelResponse],
    reference_results: list[ExamModelResponse] | None = None,
    curriculum: Curriculum,
    exam_date: str | None,
) -> ExamAnalysis:
    intake = _normalize(intake, ExamIntake)
    curriculum = _normalize(curriculum, Curriculum)
    papers = _stage_results(paper_results, "paper")
    response_stages = _stage_results(response_results, "response")
    diagnoses = _stage_results(diagnosis_results, "diagnosis")
    references = _stage_results(reference_results or [], "reference")
    if exam_date is not None:
        try:
            exam_date = date.fromisoformat(exam_date).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("exam_date must be an ISO calendar date or None") from exc
    if not curriculum.version.strip():
        raise ValueError("curriculum version is empty")
    curriculum_index = CurriculumIndex(curriculum)
    curriculum_ids = set(curriculum_index.points)
    pages = {page.id: page for page in intake.pages}
    warnings: list[str] = []
    for result in papers + response_stages + diagnoses + references:
        warnings.extend(result.warnings)
    if exam_date is None:
        warnings.append("exam date is unknown; chronology cannot be established")

    paper_records: list[PaperQuestion] = []
    score_rules: list[SectionScoreRule] = []
    for result in papers:
        for question in result.questions:
            if not _valid_evidence_list(question.sources, pages, _PAPER_EVIDENCE):
                warnings.append(
                    f"question {question.number}: source evidence is missing or invalid"
                )
                continue
            points, bad_points = _valid_ids(question.point_ids, curriculum_ids)
            if bad_points:
                warnings.append(f"question {question.number}: unknown curriculum point rejected")
            score_sources = [
                evidence
                for evidence in question.score_sources
                if _evidence_ok(evidence, pages, _PAPER_SCORE_EVIDENCE)
            ]
            if question.max_score is not None and not score_sources:
                warnings.append(
                    f"question {question.number}: maximum score lacks valid score evidence"
                )
            paper_records.append(
                question.model_copy(
                    update={"point_ids": points, "score_sources": score_sources}, deep=True
                )
            )
        for rule in result.scoring_rules:
            if not _valid_evidence_list(rule.sources, pages, _PAPER_SCORE_EVIDENCE):
                warnings.append("scoring rule ignored: source evidence is missing or invalid")
                continue
            quote = rule.literal.strip()
            per_question_phrase = re.search(r"(?:每(?:小题|道题|题)|各题)", quote) is not None
            source_quote = bool(quote) and _adjacent_header_quote(quote, rule.sources, pages)
            if not per_question_phrase or not source_quote:
                warnings.append(
                    "scoring rule ignored: per-question scope is not proven by its quoted source"
                )
                continue
            score_rules.append(rule)

    part_maps = _canonical_part_maps(paper_records, warnings)
    paper_records = [
        question.model_copy(
            update={
                "part_paths": [
                    _map_part_path(question.number, path, part_maps) for path in question.part_paths
                ]
            },
            deep=True,
        )
        for question in paper_records
    ]

    response_records: list[StudentResponse] = []
    for result in response_stages:
        for response in result.responses:
            if not _valid_evidence_list(response.sources, pages, _RESPONSE_EVIDENCE):
                warnings.append(f"response {response.id}: source evidence is missing or invalid")
                continue
            paper_sources = [
                evidence
                for evidence in response.paper_sources
                if _evidence_ok(evidence, pages, _PAPER_EVIDENCE)
            ]
            if len(paper_sources) != len(response.paper_sources):
                warnings.append(f"response {response.id}: invalid paper association evidence")
            response_records.append(
                response.model_copy(update={"paper_sources": paper_sources}, deep=True)
            )
    response_records = [
        response.model_copy(
            update={
                "part_path": _map_part_path(response.number, response.part_path, part_maps),
                "grading": [
                    observation.model_copy(
                        update={
                            "part_path": _map_part_path(
                                response.number, observation.part_path, part_maps
                            )
                        },
                        deep=True,
                    )
                    for observation in response.grading
                ],
            },
            deep=True,
        )
        for response in response_records
    ]
    if len({response.id for response in response_records}) < len(response_records):
        warnings.append("duplicate response IDs make their associations ambiguous")

    diagnosis_records: list[DiagnosisProposal] = []
    for result in diagnoses:
        diagnosis_records.extend(
            proposal.model_copy(
                update={
                    "part_path": _map_part_path(proposal.number, proposal.part_path, part_maps)
                },
                deep=True,
            )
            for proposal in result.diagnoses
        )
    reference_records: list[ReferenceAnswer] = []
    for result in references:
        for answer in result.reference_answers:
            answer = answer.model_copy(
                update={"part_path": _map_part_path(answer.number, answer.part_path, part_maps)},
                deep=True,
            )
            matching_paper = [item for item in paper_records if item.number == answer.number]
            valid_path = not answer.part_path or (
                len(matching_paper) == 1 and answer.part_path in matching_paper[0].part_paths
            )
            if (
                answer.number in intake.expected_question_numbers
                and _valid_evidence_list(answer.sources, pages, {"reference": {"reference"}})
                and valid_path
            ):
                reference_records.append(answer)
            else:
                warnings.append(
                    f"reference answer {answer.number}: source, role, or manifest number is invalid"
                )
    if not reference_records:
        warnings.append(
            "no validated reference answer was supplied; unmarked responses remain ungraded"
        )
    extra_numbers = sorted(
        {question.number for question in paper_records} - set(intake.expected_question_numbers)
    )
    if extra_numbers:
        warnings.append("model extracted question numbers outside the fixed intake manifest")

    observed_totals: list[GradingObservation] = []
    for result in papers + response_stages:
        for observation in result.grading:
            if observation.kind not in {"total_awarded", "total_maximum"}:
                if result.task == "paper" and observation.kind == "maximum":
                    continue
                warnings.append(
                    "unscoped batch-level grading observation ignored for per-question ledger"
                )
                continue
            if _valid_evidence_list(observation.sources, pages, _TOTAL_EVIDENCE):
                observed_totals.append(observation)
            else:
                warnings.append("whole-paper total ignored: grading evidence is invalid")
    observed_totals = _dedupe_observations(observed_totals)

    output_questions: list[AnalyzedQuestion] = []
    for number in intake.expected_question_numbers:
        q_warnings: list[str] = []
        q_papers = [question for question in paper_records if question.number == number]
        association, paper, linked_responses = _associate_question(
            number, paper_records, response_records, pages
        )
        if len(q_papers) > 1:
            q_warnings.append("multiple source questions share this number")
        elif paper is None:
            q_warnings.append("printed question was not identified from the paper")
        responses_for_number = [
            response for response in response_records if response.number == number
        ]
        if association.status == "ambiguous":
            q_warnings.append("response association is ambiguous")
        if not responses_for_number:
            q_warnings.append("student response is missing; score and outcome remain unknown")
        if paper is not None and paper.completeness == "partial":
            q_warnings.append("printed question is partial; no completeness inference is made")
        paper_sources = association.paper_sources
        response_sources = association.response_sources
        assoc_status = association.status

        point_ids: list[str] = []
        if assoc_status == "linked" and paper is not None:
            point_ids.extend(paper.point_ids)
        q_diagnoses = [item for item in diagnosis_records if item.number == number]
        accepted_diagnoses: list[DiagnosisProposal] = []
        supported_diagnosis_paths: set[str] = set()
        if assoc_status == "linked":
            for proposal in q_diagnoses:
                proposal_points, bad_points = _valid_ids(proposal.point_ids, curriculum_ids)
                evidence_valid = _valid_evidence_list(proposal.evidence, pages, _DIAGNOSIS_EVIDENCE)
                if bad_points:
                    q_warnings.append("diagnosis with out-of-curriculum points was rejected")
                    continue
                if not evidence_valid:
                    q_warnings.append("diagnosis with missing or invalid evidence was rejected")
                    continue
                proposal_responses = [
                    response
                    for response in linked_responses
                    if not proposal.part_path
                    or not response.part_path
                    or response.part_path == proposal.part_path
                ]
                handwriting = [
                    evidence
                    for evidence in proposal.evidence
                    if evidence.kind == "handwriting"
                    and pages[evidence.page_id].role == "response"
                    and any(
                        _paper_response_overlap(response.sources, [evidence], pages)
                        for response in proposal_responses
                    )
                ]
                paper_bound = all(
                    pages[evidence.page_id].role != "paper"
                    or _paper_response_overlap(paper_sources, [evidence], pages)
                    for evidence in proposal.evidence
                )
                response_bound = all(
                    pages[evidence.page_id].role != "response"
                    or any(
                        _paper_response_overlap(response.sources, [evidence], pages)
                        for response in proposal_responses
                    )
                    for evidence in proposal.evidence
                )
                matching_keys = [
                    answer
                    for answer in reference_records
                    if answer.number == number and answer.part_path == proposal.part_path
                ]
                key_evidence = [
                    evidence
                    for evidence in proposal.evidence
                    if pages[evidence.page_id].role == "reference"
                    and any(
                        _paper_response_overlap(answer.sources, [evidence], pages)
                        for answer in matching_keys
                    )
                ]
                reference_bound = all(
                    pages[evidence.page_id].role != "reference"
                    or any(
                        _paper_response_overlap(answer.sources, [evidence], pages)
                        for answer in matching_keys
                    )
                    for evidence in proposal.evidence
                )
                if not handwriting or not paper_bound or not response_bound or not reference_bound:
                    q_warnings.append(
                        "diagnosis without the matched response's handwriting region was rejected"
                    )
                    continue
                grade_support = any(
                    observation.part_path == proposal.part_path
                    and observation.kind in {"mark", "awarded", "deducted"}
                    and _valid_evidence_list(observation.sources, pages, _GRADING_EVIDENCE)
                    and any(
                        _paper_response_overlap(response.sources, observation.sources, pages)
                        for response in proposal_responses
                    )
                    for response in proposal_responses
                    for observation in response.grading
                )
                key_support = bool(matching_keys and key_evidence)
                if grade_support or key_support:
                    supported_diagnosis_paths.add(proposal.part_path)
                    accepted_diagnoses.append(
                        proposal.model_copy(update={"point_ids": proposal_points})
                    )
                else:
                    accepted_diagnoses.append(
                        proposal.model_copy(
                            update={"point_ids": proposal_points, "reason_status": "hypothesis"}
                        )
                    )
        part_paths: dict[str, list[DiagnosisProposal]] = defaultdict(list)
        for proposal in accepted_diagnoses:
            part_paths[proposal.part_path].append(proposal)
        valid_paths = set(paper.part_paths) | {""} if paper is not None else {""}
        selected_diagnoses: list[DiagnosisProposal] = []
        for part_path, proposals in part_paths.items():
            if part_path not in valid_paths:
                q_warnings.append(
                    f"diagnosis for unknown subquestion path {part_path!r} was rejected"
                )
            elif len(proposals) != 1:
                q_warnings.append(
                    f"multiple diagnoses for subquestion path {part_path!r} are ambiguous"
                )
            else:
                selected_diagnoses.append(proposals[0])
        selected_diagnoses.sort(
            key=lambda item: (
                item.part_path != "",
                (
                    paper.part_paths.index(item.part_path)
                    if paper and item.part_path in paper.part_paths
                    else -1
                ),
            )
        )
        if selected_diagnoses:
            point_ids = [point for proposal in selected_diagnoses for point in proposal.point_ids]
        point_ids = [point.id for point in curriculum.points if point.id in set(point_ids)]

        q_observations: list[GradingObservation] = []
        linked_page_ids = _source_page_ids(response_sources) if linked_responses else set()
        if linked_responses:
            for response in linked_responses:
                for observation in response.grading:
                    if _valid_evidence_list(observation.sources, pages, _GRADING_EVIDENCE) and (
                        _source_page_ids(observation.sources) <= linked_page_ids
                    ):
                        q_observations.append(observation)
                    else:
                        q_warnings.append("unbound per-question grading observation ignored")
            q_observations = _dedupe_observations(q_observations)
        if paper is not None:
            for result in papers:
                for observation in result.grading:
                    if (
                        observation.kind == "maximum"
                        and _valid_evidence_list(observation.sources, pages, _MAXIMUM_EVIDENCE)
                        and _paper_response_overlap(paper.sources, observation.sources, pages)
                    ):
                        q_observations.append(observation)
            q_observations = _dedupe_observations(q_observations)

        score_sources: list[ExamEvidence] = []
        paper_maxima: list[float] = []
        if (
            paper is not None
            and paper.max_score is not None
            and paper.score_sources
            and paper.completeness == "complete"
        ):
            paper_maxima.append(float(paper.max_score))
            score_sources.extend(paper.score_sources)
        for rule in score_rules:
            if rule.scope == "per_question" and number in rule.question_numbers:
                paper_maxima.append(float(rule.max_score))
                score_sources.extend(rule.sources)
        root_observations = [
            observation for observation in q_observations if not observation.part_path
        ]
        grading_maximum = _single_observed_value(
            root_observations, "maximum", q_warnings, "maximum"
        )
        if grading_maximum is not None:
            paper_maxima.append(grading_maximum)
            score_sources.extend(
                evidence
                for observation in q_observations
                if observation.kind == "maximum"
                for evidence in observation.sources
            )
        unique_maxima = set(paper_maxima)
        maximum = next(iter(unique_maxima)) if len(unique_maxima) == 1 else None
        if len(unique_maxima) > 1:
            q_warnings.append("paper and grading maximum scores conflict; maximum left unknown")
        awarded = _single_observed_value(root_observations, "awarded", q_warnings, "awarded score")
        deducted = _single_observed_value(root_observations, "deducted", q_warnings, "deduction")
        if awarded is None and maximum is not None and deducted is not None:
            if deducted <= maximum:
                awarded = maximum - deducted
            else:
                q_warnings.append("deduction exceeds maximum; score ledger unresolved")
        if (
            maximum is not None
            and awarded is not None
            and deducted is not None
            and awarded + deducted > maximum
        ):
            q_warnings.append(
                "awarded plus deducted score exceeds maximum; score ledger unresolved"
            )
            awarded = deducted = None
        if assoc_status != "linked":
            awarded = deducted = None

        root_observations = [
            observation for observation in q_observations if not observation.part_path
        ]
        observed_marks = sorted(
            {observation.mark for observation in root_observations if observation.kind == "mark"}
        )
        mark_outcome = (
            {"correct": "correct", "incorrect": "incorrect", "partial": "partial"}.get(
                observed_marks[0], "unknown"
            )
            if len(observed_marks) == 1
            else "unknown"
        )
        if len(observed_marks) > 1:
            q_warnings.append("conflicting grading marks leave outcome unknown")
        elif (
            not observed_marks
            and linked_responses
            and all(response.legibility == "blank" for response in linked_responses)
        ):
            mark_outcome = "unanswered"
        root_diagnoses = [proposal for proposal in selected_diagnoses if not proposal.part_path]
        child_diagnoses = [proposal for proposal in selected_diagnoses if proposal.part_path]
        supported_root = [
            proposal
            for proposal in root_diagnoses
            if proposal.part_path in supported_diagnosis_paths
        ]
        supported_children = [
            proposal
            for proposal in child_diagnoses
            if proposal.part_path in supported_diagnosis_paths
        ]
        diagnosis_outcome = (
            supported_root[0].outcome
            if len(supported_root) == 1
            else _root_outcome([item.outcome for item in supported_children])
            if paper is not None
            and paper.part_paths
            and set(item.part_path for item in supported_children) == set(paper.part_paths)
            else "unknown"
        )
        if assoc_status != "linked":
            outcome = "unknown"
        elif maximum is not None and awarded is not None:
            if awarded > maximum:
                outcome = "unknown"
                q_warnings.append("awarded score exceeds maximum; outcome left unknown")
            elif awarded == maximum:
                outcome = "correct"
            elif awarded == 0:
                outcome = "incorrect"
            else:
                outcome = "partial"
            if mark_outcome != "unknown" and mark_outcome != outcome:
                q_warnings.append(
                    "grading mark differs from explicit score ledger; numeric score used"
                )
            if diagnosis_outcome != "unknown" and diagnosis_outcome != outcome:
                q_warnings.append("numeric score differs from model diagnosis; explicit score used")
        elif mark_outcome != "unknown":
            outcome = mark_outcome
            if diagnosis_outcome != "unknown" and mark_outcome != diagnosis_outcome:
                q_warnings.append("grading mark differs from model diagnosis; explicit mark used")
        elif diagnosis_outcome != "unknown":
            outcome = diagnosis_outcome
        else:
            outcome = mark_outcome

        reason = ""
        reason_status = "unknown"
        suggested_action = ""
        diagnosis_evidence: list[ExamEvidence] = []
        if selected_diagnoses:
            reason = "；".join(
                f"({proposal.part_path}) {proposal.reason}"
                if proposal.part_path
                else proposal.reason
                for proposal in (root_diagnoses or selected_diagnoses)
                if proposal.reason
            )
            status_set = {
                proposal.reason_status for proposal in (root_diagnoses or selected_diagnoses)
            }
            reason_status = (
                "supported"
                if status_set == {"supported"}
                else "hypothesis"
                if "hypothesis" in status_set
                else "unknown"
            )
            suggested_action = "；".join(
                proposal.suggested_action
                for proposal in (root_diagnoses or selected_diagnoses)
                if proposal.suggested_action
            )
            diagnosis_evidence = [
                evidence for proposal in selected_diagnoses for evidence in proposal.evidence
            ]
        evidence = _unique_evidence(paper_sources + response_sources + diagnosis_evidence)
        q_warnings = list(dict.fromkeys(q_warnings))
        output_questions.append(
            AnalyzedQuestion(
                number=number,
                outcome=outcome,
                association=association,
                response_ids=association.response_ids,
                awarded=awarded,
                deducted=deducted,
                maximum=maximum,
                grading_evidence=_dedupe_observations(q_observations),
                point_ids=point_ids,
                part_diagnoses=selected_diagnoses,
                reason=reason,
                reason_status=reason_status,
                evidence=evidence,
                suggested_action=suggested_action,
                warnings=q_warnings,
            )
        )
        warnings.extend(f"question {number}: {warning}" for warning in q_warnings)

    all_numbers_linked = all(
        question.association.status == "linked" for question in output_questions
    )
    all_awarded = all(question.awarded is not None for question in output_questions)
    all_maximum = all(question.maximum is not None for question in output_questions)
    total_awarded = (
        sum(float(question.awarded) for question in output_questions)
        if all_numbers_linked and all_awarded
        else None
    )
    total_maximum = (
        sum(float(question.maximum) for question in output_questions)
        if all_numbers_linked and all_maximum
        else None
    )
    payload = {
        "schema_version": "exam-analysis-v1",
        "exam_id": intake.exam_id,
        "student_id": intake.student_id,
        "exam_date": exam_date,
        "curriculum_version": curriculum.version,
        "curriculum_sha256": _canonical_sha(curriculum),
        "revision": "pending",
        "parent_revision": None,
        "is_synthetic_student": intake.is_synthetic_student,
        "input_sha256": _canonical_sha(
            {
                "intake": intake,
                "paper_results": papers,
                "response_results": response_stages,
                "diagnosis_results": diagnoses,
                "reference_results": references,
                "curriculum": curriculum,
                "exam_date": exam_date,
            }
        ),
        "source_material_sha256": _canonical_sha(
            sorted({(material.role, material.sha256) for material in intake.materials})
        ),
        "expected_question_numbers": intake.expected_question_numbers,
        "paper_questions": paper_records,
        "student_responses": response_records,
        "reference_answers": reference_records,
        "questions": output_questions,
        "total_awarded": total_awarded,
        "total_maximum": total_maximum,
        "observed_totals": observed_totals,
        "warnings": list(dict.fromkeys(warnings)),
        "correction_ids": [],
    }
    payload["revision"] = _analysis_revision(payload)
    return ExamAnalysis.model_validate(payload)


def apply_exam_corrections(
    analysis: ExamAnalysis,
    corrections: list[ExamCorrection],
    *,
    intake: ExamIntake,
    curriculum: Curriculum,
) -> ExamAnalysis:
    analysis = _normalize(analysis, ExamAnalysis)
    intake = _normalize(intake, ExamIntake)
    curriculum = _normalize(curriculum, Curriculum)
    if analysis.exam_id != intake.exam_id or analysis.student_id != intake.student_id:
        raise ValueError("correction intake does not match the analysis")
    if analysis.curriculum_version != curriculum.version:
        raise ValueError("correction curriculum version differs from the analysis")
    if analysis.curriculum_sha256 != _canonical_sha(curriculum):
        raise ValueError("correction curriculum content differs from the analysis")
    if not isinstance(corrections, list) or not corrections:
        raise ValueError("at least one correction is required")
    normalized = [_normalize(item, ExamCorrection) for item in corrections]
    if any(item.base_revision != analysis.revision for item in normalized):
        raise ValueError("correction base revision is stale")
    correction_ids = [item.id for item in normalized]
    if len(correction_ids) != len(set(correction_ids)) or set(correction_ids) & set(
        analysis.correction_ids
    ):
        raise ValueError("correction identifiers must be new and unique")
    corrected_numbers = [item.number for item in normalized]
    if len(corrected_numbers) != len(set(corrected_numbers)):
        raise ValueError("submit at most one correction per question in a revision")
    index = CurriculumIndex(curriculum)
    page_map = {page.id: page for page in intake.pages}
    paper_by_number: dict[int, list[PaperQuestion]] = defaultdict(list)
    for question in analysis.paper_questions:
        paper_by_number[question.number].append(question)
    question_by_number = {question.number: question for question in analysis.questions}
    response_list = [response.model_copy(deep=True) for response in analysis.student_responses]
    responses_by_id: dict[str, list[int]] = defaultdict(list)
    for position, response in enumerate(response_list):
        responses_by_id[response.id].append(position)
    selected_overrides: dict[int, list[str]] = {}
    affected_numbers: set[int] = set()
    correction_evidence: dict[int, list[ExamEvidence]] = defaultdict(list)
    for correction in normalized:
        if correction.number not in question_by_number:
            raise ValueError("correction refers to a number outside the fixed manifest")
        if correction.point_ids is not None:
            index.validate_ids(correction.point_ids)
        q = question_by_number[correction.number]
        target_papers = paper_by_number.get(correction.number, [])
        if len(target_papers) != 1:
            raise ValueError("correction target needs one unique source question")
        target_paper = target_papers[0]
        for evidence in correction.evidence:
            if not _evidence_ok(evidence, page_map, _DIAGNOSIS_EVIDENCE):
                raise ValueError("correction evidence is not bound to an intake source")
        if correction.response_id and correction.response_ids is not None:
            raise ValueError("use either response_id or response_ids, not both")
        if correction.response_ids is not None:
            candidates = set(q.association.response_ids)
            if not set(correction.response_ids) <= candidates:
                raise ValueError("correction may only select existing response candidates")
            selected_overrides[correction.number] = list(correction.response_ids)
            if correction.response_ids:
                if len(correction.response_ids) > 1:
                    raise ValueError("manual association correction must select one response ID")
                selected_id = correction.response_ids[0]
                locations = responses_by_id.get(selected_id, [])
                if len(locations) != 1:
                    raise ValueError("selected response ID is missing or duplicated")
                response = response_list[locations[0]]
                paper_evidence = [
                    e for e in correction.evidence if page_map[e.page_id].role == "paper"
                ]
                response_evidence = [
                    e for e in correction.evidence if page_map[e.page_id].role == "response"
                ]
                if not response_evidence or not _paper_response_overlap(
                    target_paper.sources, paper_evidence, page_map
                ):
                    raise ValueError(
                        "association correction needs overlapping paper and response evidence"
                    )
                if not _paper_response_overlap(response.sources, response_evidence, page_map):
                    raise ValueError(
                        "association correction response evidence does not bind to the candidate"
                    )
                response_list[locations[0]] = response.model_copy(
                    update={
                        "paper_match": "matched",
                        "paper_sources": paper_evidence,
                        "association_reason": "teacher selected this candidate with overlapping source evidence",
                    },
                    deep=True,
                )
            affected_numbers.add(correction.number)
        if correction.response_id is not None:
            locations = responses_by_id.get(correction.response_id, [])
            if len(locations) != 1:
                raise ValueError("correction response ID is missing or duplicated")
            position = locations[0]
            response = response_list[position]
            destination = correction.corrected_response_number or response.number
            if destination != correction.number:
                raise ValueError(
                    "response correction target must equal its corrected question number"
                )
            if destination not in intake.expected_question_numbers:
                raise ValueError("corrected response number is outside the fixed manifest")
            destination_papers = paper_by_number.get(destination, [])
            if len(destination_papers) != 1:
                raise ValueError("corrected response target needs one unique source question")
            next_part = (
                correction.corrected_part_path
                if correction.corrected_part_path is not None
                else response.part_path
            )
            if next_part and next_part not in destination_papers[0].part_paths:
                raise ValueError("corrected part path is not in the target question")
            paper_evidence = [e for e in correction.evidence if page_map[e.page_id].role == "paper"]
            response_evidence = [
                e for e in correction.evidence if page_map[e.page_id].role == "response"
            ]
            if not response_evidence:
                raise ValueError("response correction needs response-page evidence")
            if correction.corrected_response_number is not None or response.number != destination:
                if not _paper_response_overlap(
                    destination_papers[0].sources, paper_evidence, page_map
                ):
                    raise ValueError(
                        "renumbering needs overlapping evidence on the target paper question"
                    )
                if not _paper_response_overlap(response.sources, response_evidence, page_map):
                    raise ValueError(
                        "renumbering response evidence does not bind to the selected response"
                    )
                affected_numbers.update({response.number, destination})
                response_paper_sources = paper_evidence
                response_match = "matched"
            else:
                response_paper_sources = response.paper_sources
                response_match = response.paper_match
                affected_numbers.add(destination)
            update = {
                "number": destination,
                "part_path": next_part,
                "paper_match": response_match,
                "paper_sources": response_paper_sources,
                "association_reason": "teacher corrected response transcription or association",
            }
            if correction.corrected_transcript is not None:
                update["transcript"] = correction.corrected_transcript
            response_list[position] = response.model_copy(update=update, deep=True)
            selected_overrides[destination] = [correction.response_id]
        elif any(
            value is not None
            for value in (
                correction.corrected_transcript,
                correction.corrected_response_number,
                correction.corrected_part_path,
            )
        ):
            raise ValueError("response transcription or number correction requires response_id")
        if correction.part_path and correction.part_path not in target_paper.part_paths:
            raise ValueError("correction part path is not in the target question")
        for evidence in correction.evidence:
            if page_map[evidence.page_id].role == "paper":
                if not _paper_response_overlap(target_paper.sources, [evidence], page_map):
                    raise ValueError(
                        "correction paper evidence does not overlap the target question"
                    )
            elif page_map[evidence.page_id].role == "response":
                old_sources = q.association.response_sources
                known_response_sources = old_sources + [
                    source
                    for response in response_list
                    if response.id == correction.response_id
                    for source in response.sources
                ]
                if known_response_sources and not _paper_response_overlap(
                    known_response_sources, [evidence], page_map
                ):
                    raise ValueError(
                        "correction response evidence does not overlap this question response"
                    )
        correction_evidence[correction.number].extend(correction.evidence)
        if (
            correction.outcome is not None
            or correction.point_ids is not None
            or correction.reason is not None
        ):
            affected_numbers.add(correction.number)

    # Rebuild both sides of every association after response edits, then invalidate stale diagnoses.
    rebuilt: list[AnalyzedQuestion] = []
    for question in analysis.questions:
        override = selected_overrides.get(question.number)
        association, paper, linked = _associate_question(
            question.number,
            analysis.paper_questions,
            response_list,
            page_map,
            selected_ids=override,
        )
        changed = (
            question.number in affected_numbers
            or association.status != question.association.status
            or association.response_ids != question.association.response_ids
        )
        if changed:
            affected_numbers.add(question.number)
            warnings_for_q = list(
                dict.fromkeys(
                    question.warnings
                    + ["source correction invalidated prior diagnosis; reassessment required"]
                )
            )
            question = question.model_copy(
                update={
                    "outcome": "unknown",
                    "association": association,
                    "response_ids": association.response_ids,
                    "awarded": None,
                    "deducted": None,
                    "grading_evidence": [],
                    "part_diagnoses": [],
                    "reason": "",
                    "reason_status": "unknown",
                    "suggested_action": "",
                    "point_ids": paper.point_ids if paper else [],
                    "evidence": _unique_evidence(
                        association.paper_sources + association.response_sources
                    ),
                    "warnings": warnings_for_q,
                },
                deep=True,
            )
        else:
            question = question.model_copy(
                update={"association": association, "response_ids": association.response_ids},
                deep=True,
            )
        rebuilt.append(question)
    q_positions = {question.number: index for index, question in enumerate(rebuilt)}
    for correction in normalized:
        position = q_positions[correction.number]
        question = rebuilt[position]
        if question.association.status != "linked" and correction.outcome not in (None, "unknown"):
            raise ValueError("cannot correct an outcome without a uniquely linked response")
        correction_ev = _unique_evidence(correction_evidence[correction.number])
        if correction.part_path:
            existing = {proposal.part_path: proposal for proposal in question.part_diagnoses}
            previous = existing.get(correction.part_path)
            proposal = DiagnosisProposal(
                number=correction.number,
                part_path=correction.part_path,
                outcome=correction.outcome or (previous.outcome if previous else "unknown"),
                point_ids=(
                    correction.point_ids
                    if correction.point_ids is not None
                    else (previous.point_ids if previous else [])
                ),
                reason=correction.reason
                if correction.reason is not None
                else (previous.reason if previous else ""),
                reason_status=correction.reason_status
                or (previous.reason_status if previous else "unknown"),
                evidence=correction_ev,
                suggested_action=previous.suggested_action if previous else "",
            )
            existing[correction.part_path] = proposal
            part_diagnoses = [
                existing[path]
                for path in (paper_by_number[correction.number][0].part_paths)
                if path in existing
            ]
            combined_points = set(question.point_ids)
            if correction.point_ids is not None:
                combined_points.update(correction.point_ids)
            updated = question.model_copy(
                update={
                    "part_diagnoses": part_diagnoses,
                    "point_ids": [p.id for p in curriculum.points if p.id in combined_points],
                    "evidence": _unique_evidence(question.evidence + correction_ev),
                },
                deep=True,
            )
        else:
            updated = question.model_copy(
                update={
                    "outcome": correction.outcome
                    if correction.outcome is not None
                    else question.outcome,
                    "point_ids": correction.point_ids
                    if correction.point_ids is not None
                    else question.point_ids,
                    "reason": correction.reason
                    if correction.reason is not None
                    else question.reason,
                    "reason_status": correction.reason_status or question.reason_status,
                    "evidence": _unique_evidence(question.evidence + correction_ev),
                },
                deep=True,
            )
        rebuilt[position] = updated.model_copy(
            update={
                "warnings": list(dict.fromkeys(updated.warnings + ["teacher correction appended"])),
            },
            deep=True,
        )

    total_awarded = (
        sum(float(question.awarded) for question in rebuilt)
        if all(
            question.association.status == "linked" and question.awarded is not None
            for question in rebuilt
        )
        else None
    )
    total_maximum = (
        sum(float(question.maximum) for question in rebuilt)
        if all(
            question.association.status == "linked" and question.maximum is not None
            for question in rebuilt
        )
        else None
    )
    payload = analysis.model_dump(mode="python")
    payload.update(
        {
            "parent_revision": analysis.revision,
            "questions": rebuilt,
            "student_responses": response_list,
            "total_awarded": total_awarded,
            "total_maximum": total_maximum,
            "correction_ids": analysis.correction_ids + correction_ids,
        }
    )
    payload["revision"] = "pending"
    payload["revision"] = _analysis_revision(payload)
    return ExamAnalysis.model_validate(payload)


def summarize_learning(
    analyses: list[ExamAnalysis],
    *,
    student_id: str,
    curriculum: Curriculum,
    exam_ids: list[str] | None = None,
    limit: int = 3,
) -> LearningSummary:
    curriculum = _normalize(curriculum, Curriculum)
    CurriculumIndex(curriculum)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be a positive bounded integer")
    if not isinstance(analyses, list):
        raise ValueError("analyses must be a list")
    records = [_normalize(item, ExamAnalysis) for item in analyses]
    if any(item.student_id != student_id for item in records):
        raise ValueError("all analyses must belong to the requested student")
    if any(item.curriculum_version != curriculum.version for item in records):
        raise ValueError("all analyses must use the current curriculum version")
    curriculum_sha = _canonical_sha(curriculum)
    if any(item.curriculum_sha256 != curriculum_sha for item in records):
        raise ValueError("all analyses must use the exact current curriculum content")
    by_exam: dict[str, list[ExamAnalysis]] = defaultdict(list)
    for item in records:
        by_exam[item.exam_id].append(item)
    latest: list[ExamAnalysis] = []
    warnings: list[str] = []
    for exam_id, revisions in by_exam.items():
        unique = {item.revision: item for item in revisions}
        leaves = [
            item
            for item in unique.values()
            if not any(other.parent_revision == item.revision for other in unique.values())
        ]
        if len(leaves) != 1:
            warnings.append(f"exam {exam_id}: revision history is branched; exam excluded")
            continue
        latest.append(leaves[0])
    if exam_ids is not None:
        if len(exam_ids) != len(set(exam_ids)):
            raise ValueError("explicit exam IDs must be unique")
        unknown = set(exam_ids) - {item.exam_id for item in latest}
        if unknown:
            raise ValueError("explicit exam selection contains unknown or ambiguous exams")
        selected = [item for item in latest if item.exam_id in set(exam_ids)]
        selected.sort(key=lambda item: (item.exam_date or "", item.exam_id), reverse=True)
        if len(selected) > limit:
            warnings.append("explicit exam selection exceeds limit; newest selected exams kept")
            selected = selected[:limit]
    else:
        dated = [item for item in latest if item.exam_date is not None]
        undated = [item for item in latest if item.exam_date is None]
        dated.sort(key=lambda item: (item.exam_date, item.exam_id), reverse=True)
        if len(dated) < limit and undated:
            warnings.append("some selected exams have unknown chronology")
            undated.sort(key=lambda item: item.exam_id, reverse=True)
            selected = dated + undated[: limit - len(dated)]
        else:
            selected = dated[:limit]
            if undated:
                warnings.append("undated exams were omitted because dated exams fill the selection")
    selected.sort(key=lambda item: (item.exam_date or "", item.exam_id), reverse=True)

    # Identical imports of the same dated source set count once.
    deduped: list[ExamAnalysis] = []
    seen_imports: set[str] = set()
    for item in selected:
        identity = item.source_material_sha256
        if identity in seen_imports:
            warnings.append("duplicate exam import was omitted from the learning summary")
            continue
        seen_imports.add(identity)
        deduped.append(item)
    selected = deduped

    per_point: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"observed": 0, "errors": 0, "exam_ids": [], "evidence": []}
    )
    unresolved = 0
    curriculum_ids = {point.id for point in curriculum.points}
    for analysis in selected:
        for question in analysis.questions:
            if question.outcome == "unknown" or question.association.status != "linked":
                unresolved += 1
                continue
            points, bad = _valid_ids(question.point_ids, curriculum_ids)
            if bad:
                warnings.append(
                    f"exam {analysis.exam_id} question {question.number}: invalid point ignored"
                )
            for point_id in points:
                record = per_point[point_id]
                record["observed"] += 1
                if question.outcome in {"incorrect", "partial", "unanswered"}:
                    record["errors"] += 1
                if analysis.exam_id not in record["exam_ids"]:
                    record["exam_ids"].append(analysis.exam_id)
                record["evidence"].extend(question.evidence)
    points = []
    for point in curriculum.points:
        record = per_point.get(point.id)
        if not record or record["observed"] == 0:
            continue
        points.append(
            LearningPointSummary(
                point_id=point.id,
                observed_question_count=record["observed"],
                error_question_count=record["errors"],
                exam_ids=record["exam_ids"],
                evidence=_unique_evidence(record["evidence"]),
            )
        )
    return LearningSummary(
        student_id=student_id,
        curriculum_version=curriculum.version,
        selected_exam_ids=[item.exam_id for item in selected],
        requested_exam_count=limit,
        actual_exam_count=len(selected),
        points=points,
        unresolved_question_count=unresolved,
        warnings=list(dict.fromkeys(warnings)),
    )
