"""Manifest-first offline evaluation with explicit denominators and provenance."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract

SampleKind = Literal["synthetic", "real"]
ParseStatus = Literal["parsed", "undetermined", "invalid_json", "timeout", "provider_error"]


class ExpectedQuestion(Contract):
    id: str


class ExpectedAssociation(Contract):
    id: str
    question_id: str
    subquestion_id: str | None = None


class ManifestSample(Contract):
    id: str
    kind: SampleKind
    submitted: bool = True
    expected_questions: list[ExpectedQuestion] = Field(default_factory=list)
    expected_associations: list[ExpectedAssociation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        question_ids = [item.id for item in self.expected_questions]
        association_ids = [item.id for item in self.expected_associations]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("manifest question identifiers must be unique within a sample")
        if len(association_ids) != len(set(association_ids)):
            raise ValueError("manifest association identifiers must be unique within a sample")
        if any(item.question_id not in set(question_ids) for item in self.expected_associations):
            raise ValueError("manifest association references an unknown question")
        return self


class EvaluationManifest(Contract):
    version: Literal["p0-eval-v1"] = "p0-eval-v1"
    samples: list[ManifestSample] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_samples(self):
        ids = [sample.id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest sample identifiers must be unique")
        return self


class PredictedItem(Contract):
    question_id: str | None = None
    subquestion_id: str | None = None
    association_id: str | None = None
    association_status: Literal["determined", "undetermined"] = "determined"
    # Set only by an independent ground-truth adjudicator, never copied from the model.
    adjudicated_complete: bool | None = None


class SamplePrediction(Contract):
    sample_id: str
    parse_status: ParseStatus
    items: list[PredictedItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def failed_run_has_no_prediction_items(self):
        if self.parse_status in {"invalid_json", "timeout", "provider_error"} and self.items:
            raise ValueError("failed parse status cannot contain prediction items")
        return self


class PredictionSet(Contract):
    runs: list[SamplePrediction] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_runs(self):
        ids = [run.sample_id for run in self.runs]
        if len(ids) != len(set(ids)):
            raise ValueError("prediction file must contain at most one run per sample")
        return self


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _evaluate_kind(
    kind: SampleKind,
    samples: list[ManifestSample],
    runs: dict[str, SamplePrediction],
) -> dict[str, object]:
    submitted = [sample for sample in samples if sample.submitted]
    sample_ids = {sample.id for sample in submitted}
    per_sample_questions: dict[str, list[PredictedItem]] = defaultdict(list)
    all_expected_questions: set[tuple[str, str]] = set()
    all_expected_associations: dict[tuple[str, str], tuple[str, str | None]] = {}
    unknown_run_ids = sorted(set(runs) - sample_ids)
    for sample in submitted:
        for question in sample.expected_questions:
            all_expected_questions.add((sample.id, question.id))
        for association in sample.expected_associations:
            all_expected_associations[(sample.id, association.id)] = (
                association.question_id,
                association.subquestion_id,
            )
        run = runs.get(sample.id)
        if run:
            per_sample_questions[sample.id].extend(run.items)

    parseable_statuses = {"parsed", "undetermined"}
    parseable = sum(
        runs[sample.id].parse_status in parseable_statuses
        for sample in submitted
        if sample.id in runs
    )
    matched_questions: set[tuple[str, str]] = set()
    extra_questions: list[dict[str, str]] = []
    duplicate_questions: list[dict[str, str | int]] = []
    adjudicated_true: set[tuple[str, str]] = set()
    unadjudicated_questions = 0
    for sample in submitted:
        expected = {q.id for q in sample.expected_questions}
        seen: Counter[str] = Counter()
        labels: dict[str, list[bool | None]] = defaultdict(list)
        for item in per_sample_questions[sample.id]:
            if item.question_id is None:
                continue
            seen[item.question_id] += 1
            labels[item.question_id].append(item.adjudicated_complete)
            if item.question_id in expected:
                matched_questions.add((sample.id, item.question_id))
            else:
                extra_questions.append({"sample_id": sample.id, "question_id": item.question_id})
        for question_id, count in seen.items():
            items = [
                item for item in per_sample_questions[sample.id] if item.question_id == question_id
            ]
            # Multiple rows are a normal representation of distinct subquestion
            # links. Repeating the same link (or an unlinked question row) is a duplicate.
            association_ids = [item.association_id for item in items]
            distinct_expected_links = (
                all(value is not None for value in association_ids)
                and len(set(association_ids)) == len(association_ids)
                and all(
                    (sample.id, value) in all_expected_associations
                    and all_expected_associations[(sample.id, value)]
                    == (question_id, item.subquestion_id)
                    for value, item in zip(association_ids, items, strict=True)
                )
            )
            if count > 1 and not distinct_expected_links:
                duplicate_questions.append(
                    {"sample_id": sample.id, "question_id": question_id, "count": count}
                )
        for question_id in expected:
            flags = labels.get(question_id, [])
            duplicated = any(
                row["sample_id"] == sample.id and row["question_id"] == question_id
                for row in duplicate_questions
            )
            if flags and all(flag is True for flag in flags) and not duplicated:
                adjudicated_true.add((sample.id, question_id))
            elif not flags or all(flag is None for flag in flags):
                unadjudicated_questions += 1

    predicted_associations: list[tuple[str, PredictedItem]] = [
        (sample.id, item)
        for sample in submitted
        for item in per_sample_questions[sample.id]
        if item.association_id is not None and item.association_status == "determined"
    ]
    unresolved_association_count = sum(
        item.association_status == "undetermined"
        for sample in submitted
        for item in per_sample_questions[sample.id]
    )
    correct_association_keys: set[tuple[str, str]] = set()
    wrong_associations: list[dict[str, str | None]] = []
    duplicate_associations: list[dict[str, str | int]] = []
    association_counts: Counter[tuple[str, str]] = Counter()
    for sample_id, item in predicted_associations:
        association_id = item.association_id
        assert association_id is not None
        key = (sample_id, association_id)
        association_counts[key] += 1
        expected = all_expected_associations.get(key)
        if expected == (item.question_id, item.subquestion_id):
            correct_association_keys.add(key)
        else:
            wrong_associations.append(
                {
                    "sample_id": sample_id,
                    "association_id": association_id,
                    "question_id": item.question_id,
                }
            )
    for (sample_id, association_id), count in association_counts.items():
        if count > 1:
            duplicate_associations.append(
                {"sample_id": sample_id, "association_id": association_id, "count": count}
            )

    expected_question_count = len(all_expected_questions)
    expected_association_count = len(all_expected_associations)
    matched_assoc_count = len(correct_association_keys)
    expected_assoc_by_sample: dict[str, list[str]] = defaultdict(list)
    for sample_id, association_id in all_expected_associations:
        expected_assoc_by_sample[sample_id].append(association_id)
    missing_associations = [
        {"sample_id": sid, "association_id": aid}
        for sid, aids in expected_assoc_by_sample.items()
        for aid in aids
        if (sid, aid) not in correct_association_keys
    ]
    coverage_missing = sorted(
        (
            {"sample_id": sid, "question_id": qid}
            for sid, qid in all_expected_questions - matched_questions
        ),
        key=lambda value: (value["sample_id"], value["question_id"]),
    )
    return {
        "sample_count": len(submitted),
        "not_submitted_sample_count": len(samples) - len(submitted),
        "submitted_sample_count": len(submitted),
        "parse_coverage": _ratio(parseable, len(submitted)),
        "parse_status_counts": dict(
            Counter(runs[sample.id].parse_status for sample in submitted if sample.id in runs)
        ),
        "question_coverage": _ratio(len(matched_questions), expected_question_count),
        "question_count_denominator": expected_question_count,
        "missing_questions": coverage_missing,
        "extra_questions": extra_questions,
        "duplicate_questions": duplicate_questions,
        "ground_truth_adjudicated_complete_question_rate": _ratio(
            len(adjudicated_true), expected_question_count
        ),
        "unadjudicated_expected_questions": unadjudicated_questions,
        "association_correctness_among_predictions": _ratio(
            matched_assoc_count, len(predicted_associations)
        ),
        "association_recall": _ratio(matched_assoc_count, expected_association_count),
        "expected_association_count": expected_association_count,
        "correct_association_count": matched_assoc_count,
        "predicted_association_count": len(predicted_associations),
        "unresolved_association_count": unresolved_association_count,
        "missing_associations": missing_associations,
        "wrong_associations": wrong_associations,
        "duplicate_associations": duplicate_associations,
        "prediction_runs_for_unknown_samples": unknown_run_ids,
    }


def evaluate(manifest: EvaluationManifest, predictions: PredictionSet) -> dict[str, object]:
    """Score predictions against the independent manifest without changing its denominator."""
    run_map = {run.sample_id: run for run in predictions.runs}
    all_manifest_ids = {sample.id for sample in manifest.samples}
    # Unknown runs are visible in both category summaries without counting toward either denominator.
    output: dict[str, object] = {
        "manifest_version": manifest.version,
        "provenance": {
            "synthetic": "synthetic fixtures only demonstrate evaluator behavior; not recognition quality",
            "real": "real quality is unmeasured unless real samples and independent labels are supplied",
        },
    }
    for kind in ("synthetic", "real"):
        samples = [sample for sample in manifest.samples if sample.kind == kind]
        selected_ids = {sample.id for sample in samples}
        selected_runs = {sid: run for sid, run in run_map.items() if sid in selected_ids}
        output[kind] = _evaluate_kind(kind, samples, selected_runs)
    output["unknown_prediction_sample_ids"] = sorted(set(run_map) - all_manifest_ids)
    return output
