"""Independent exam-analysis boundaries; every record here is explicitly synthetic."""

import copy

import pytest
from app.exam_contracts import (
    AnalyzedQuestion,
    ExamAnalysis,
    ExamEvidence,
    ExamIntake,
    GradingObservation,
    SectionScoreRule,
)


def evidence(page="response-1", kind="grading"):
    return {"page_id": page, "bbox": [0.1, 0.1, 0.8, 0.8], "kind": kind}


def intake_payload():
    pages = [
        {
            "id": name,
            "document_sha256": digest,
            "source_page": 1,
            "role": role,
            "image_path": f"synthetic/{name}.png",
            "image_sha256": "c" * 64,
            "width": 100,
            "height": 200,
        }
        for name, digest, role in [
            ("paper-1", "a" * 64, "paper"),
            ("response-1", "b" * 64, "response"),
        ]
    ]
    return {
        "exam_id": "synthetic-exam",
        "student_id": "synthetic-student",
        "is_synthetic_student": True,
        "expected_question_numbers": [1, 2],
        "materials": [
            {
                "id": role,
                "sha256": digest,
                "source_path": f"synthetic/{role}.pdf",
                "role": role,
                "page_ids": [name],
            }
            for name, digest, role in [
                ("paper-1", "a" * 64, "paper"),
                ("response-1", "b" * 64, "response"),
            ]
        ],
        "pages": pages,
    }


@pytest.mark.parametrize("value", [True, "4", -1, float("nan"), float("inf")])
def test_grading_rejects_boolean_coercion_negative_and_nonfinite_values(value):
    with pytest.raises(ValueError):
        GradingObservation(kind="deducted", value=value, literal="synthetic", sources=[evidence()])


def test_deduction_is_nonnegative_magnitude_with_original_signed_literal():
    observation = GradingObservation(kind="deducted", value=4, literal="-4", sources=[evidence()])
    assert observation.value == 4 and observation.literal == "-4"
    assert observation.kind == "deducted"


@pytest.mark.parametrize("number", [True, "1", 1.0])
def test_expected_denominator_rejects_coercion_before_number_validation(number):
    data = intake_payload()
    data["expected_question_numbers"] = [number, 2]
    with pytest.raises(ValueError):
        ExamIntake.model_validate(data)


@pytest.mark.parametrize("coords", [[True, 0.0, 1.0, 1.0], [0, 0, float("nan"), 1], [0, 0, 0, 1]])
def test_evidence_coordinates_are_real_finite_ordered_values(coords):
    with pytest.raises(ValueError):
        ExamEvidence(page_id="synthetic", kind="grading", bbox=coords)


@pytest.mark.parametrize(
    "failure", ["missing_parent", "self_cycle", "wrong_role", "wrong_document"]
)
def test_crop_must_retain_original_document_role_and_acyclic_parent(failure):
    data = intake_payload()
    crop = copy.deepcopy(data["pages"][1])
    crop.update(id="response-crop", parent_page_id="response-1", parent_bbox=[0, 0, 1, 1])
    if failure == "missing_parent":
        crop["parent_page_id"] = "missing"
    elif failure == "self_cycle":
        crop["parent_page_id"] = crop["id"]
    elif failure == "wrong_role":
        crop["role"] = "paper"
    else:
        crop["document_sha256"] = "d" * 64
    data["pages"].append(crop)
    with pytest.raises(ValueError):
        ExamIntake.model_validate(data)


def test_six_derived_views_do_not_add_material_physical_pages():
    data = intake_payload()
    for number in range(6):
        crop = copy.deepcopy(data["pages"][1])
        crop.update(
            id=f"synthetic-view-{number}", parent_page_id="response-1", parent_bbox=[0, 0, 1, 1]
        )
        data["pages"].append(crop)
    intake = ExamIntake.model_validate(data)
    assert len(intake.pages) == 8
    assert sum(len(material.page_ids) for material in intake.materials) == 2
    assert sum(page.parent_page_id is None for page in intake.pages) == 2
    assert intake.expected_question_numbers == [1, 2]


def test_same_physical_page_cannot_be_repeated_under_an_alias_to_inflate_coverage():
    data = intake_payload()
    alias = copy.deepcopy(data["pages"][1])
    alias["id"] = "response-alias"
    data["pages"].append(alias)
    data["materials"][1]["page_ids"].append(alias["id"])
    with pytest.raises(ValueError):
        ExamIntake.model_validate(data)


def test_final_association_number_cannot_silently_move_to_another_question():
    with pytest.raises(ValueError):
        AnalyzedQuestion(
            number=1,
            association={
                "number": 2,
                "response_ids": [],
                "status": "unmatched",
                "reason": "synthetic",
            },
        )


@pytest.mark.parametrize("numbers", [[1], [1, 1], [1, 2, 3], [2, 1]])
def test_final_analysis_preserves_fixed_denominator_order_even_for_unresolved_questions(numbers):
    rows = [
        {
            "number": number,
            "association": {
                "number": number,
                "response_ids": [],
                "status": "unmatched",
                "reason": "synthetic unresolved",
            },
        }
        for number in numbers
    ]
    with pytest.raises(ValueError):
        ExamAnalysis(
            exam_id="synthetic-exam",
            student_id="synthetic-student",
            is_synthetic_student=True,
            exam_date=None,
            curriculum_version="synthetic-v1",
            revision="synthetic-r1",
            input_sha256="a" * 64,
            source_material_sha256="b" * 64,
            curriculum_sha256="c" * 64,
            expected_question_numbers=[1, 2],
            questions=rows,
        )


def test_total_section_points_cannot_be_disguised_as_per_question_rule():
    with pytest.raises(ValueError):
        SectionScoreRule(
            scope="section_total",
            literal="synthetic section total30",
            question_numbers=[1, 2],
            max_score=30,
            sources=[evidence("paper-1", "printed_question")],
        )


def analysis_inputs():
    from app.contracts import Curriculum
    from app.exam_contracts import ExamModelResponse

    paper_evidence = [
        {**evidence("paper-1", "printed_question"), "bbox": box}
        for box in [[0.1, 0.1, 0.4, 0.4], [0.5, 0.5, 0.9, 0.9]]
    ]
    response_evidence = [
        {**evidence("response-1", "handwriting"), "bbox": box}
        for box in [[0.1, 0.1, 0.4, 0.4], [0.5, 0.5, 0.9, 0.9]]
    ]
    paper = ExamModelResponse(
        task="paper",
        questions=[
            {
                "number": number,
                "text": f"synthetic printed question {number}",
                "sources": [paper_evidence[number - 1]],
                "max_score": 10,
                "score_sources": [paper_evidence[number - 1]],
                "point_ids": ["p1"],
            }
            for number in [1, 2]
        ],
    )
    responses = ExamModelResponse(
        task="response",
        responses=[
            {
                "id": f"synthetic-response-{number}",
                "number": number,
                "transcript": f"synthetic response {number}",
                "legibility": "readable",
                "sources": [response_evidence[number - 1]],
                "paper_sources": [paper_evidence[number - 1]],
                "paper_match": "matched",
                "association_reason": "synthetic explicit number and matching printed region",
            }
            for number in [1, 2]
        ],
    )
    return {
        "intake": ExamIntake.model_validate(intake_payload()),
        "paper_results": [paper],
        "response_results": [responses],
        "diagnosis_results": [],
        "curriculum": Curriculum.model_validate(
            {"version": "synthetic-v1", "points": [{"id": "p1", "name": "Synthetic point"}]}
        ),
        "exam_date": None,
    }


def test_parent_deduction_and_cross_mark_mean_partial_not_zero_and_do_not_backfill_others():
    from app.exam_analysis import analyze_exam

    data = analysis_inputs()
    data["response_results"][0].responses[0].grading = [
        GradingObservation(kind="deducted", value=4, literal="-4", sources=[evidence()]),
        GradingObservation(
            kind="mark", mark="incorrect", literal="synthetic cross", sources=[evidence()]
        ),
    ]
    analysis = analyze_exam(**data)
    first, second = analysis.questions
    assert (first.maximum, first.deducted, first.awarded, first.outcome) == (10, 4, 6, "partial")
    assert second.awarded is None and second.deducted is None and second.outcome == "unknown"
    assert analysis.total_awarded is None


def test_unscoped_local_grade_cannot_be_copied_to_every_question_on_same_page():
    from app.exam_analysis import analyze_exam

    data = analysis_inputs()
    data["response_results"][0].grading = [
        GradingObservation(kind="deducted", value=4, literal="-4", sources=[evidence()])
    ]
    analysis = analyze_exam(**data)
    assert all(
        question.deducted is None and question.awarded is None for question in analysis.questions
    )
    assert all(question.outcome == "unknown" for question in analysis.questions)


def test_matching_page_without_matching_question_region_is_not_a_verified_association():
    from app.exam_analysis import analyze_exam

    data = analysis_inputs()
    data["response_results"][0].responses[0].paper_sources = list(
        data["paper_results"][0].questions[1].sources
    )
    analysis = analyze_exam(**data)
    assert analysis.questions[0].association.status != "linked"
    assert analysis.questions[0].outcome == "unknown"


def test_subquestion_deduction_cannot_become_parent_deduction_or_be_divided_between_parts():
    from app.exam_analysis import analyze_exam

    data = analysis_inputs()
    data["paper_results"][0].questions[0].part_paths = ["1", "2"]
    data["response_results"][0].responses[0].part_path = "1"
    data["response_results"][0].responses[0].grading = [
        GradingObservation(
            kind="deducted", value=4, literal="-4", part_path="1", sources=[evidence()]
        )
    ]
    analysis = analyze_exam(**data)
    assert analysis.questions[0].deducted is None and analysis.questions[0].awarded is None
    assert analysis.questions[0].maximum == 10


def test_observed_total_does_not_fill_missing_question_scores():
    from app.exam_analysis import analyze_exam

    data = analysis_inputs()
    data["response_results"][0].grading = [
        GradingObservation(
            kind="total_awarded", value=19, literal="synthetic total19", sources=[evidence()]
        )
    ]
    analysis = analyze_exam(**data)
    assert len(analysis.observed_totals) == 1
    assert analysis.observed_totals[0].value == 19
    assert analysis.total_awarded is None
    assert all(
        question.awarded is None and question.outcome == "unknown"
        for question in analysis.questions
    )


def test_number_only_response_correction_is_applied_and_old_diagnosis_is_invalidated():
    from app.exam_analysis import analyze_exam, apply_exam_corrections
    from app.exam_contracts import ExamCorrection

    data = analysis_inputs()
    original = analyze_exam(**data)
    before = original.model_dump(mode="json")
    correction = ExamCorrection(
        id="synthetic-move",
        base_revision=original.revision,
        number=2,
        response_id="synthetic-response-1",
        corrected_response_number=2,
        evidence=[
            evidence("response-1", "handwriting"),
            data["paper_results"][0].questions[1].sources[0],
        ],
    )
    changed = apply_exam_corrections(
        original, [correction], intake=data["intake"], curriculum=data["curriculum"]
    )
    moved = next(
        response for response in changed.student_responses if response.id == "synthetic-response-1"
    )
    assert moved.number == 2
    assert changed.questions[0].association.status != "linked"
    assert changed.questions[0].outcome == "unknown"
    assert changed.parent_revision == original.revision and changed.revision != original.revision
    assert original.model_dump(mode="json") == before


def test_multiple_response_text_corrections_all_survive_one_new_revision():
    from app.exam_analysis import analyze_exam, apply_exam_corrections
    from app.exam_contracts import ExamCorrection

    data = analysis_inputs()
    original = analyze_exam(**data)
    corrections = [
        ExamCorrection(
            id=f"synthetic-edit-{number}",
            base_revision=original.revision,
            number=number,
            response_id=f"synthetic-response-{number}",
            corrected_transcript=f"synthetic revised {number}",
            evidence=[evidence("response-1", "handwriting")],
        )
        for number in [1, 2]
    ]
    changed = apply_exam_corrections(
        original, corrections, intake=data["intake"], curriculum=data["curriculum"]
    )
    assert [response.transcript for response in changed.student_responses] == [
        "synthetic revised 1",
        "synthetic revised 2",
    ]
    assert changed.correction_ids == ["synthetic-edit-1", "synthetic-edit-2"]


def test_one_undated_exam_is_usable_without_inventing_three_exams_or_mastery():
    from app.exam_analysis import analyze_exam, summarize_learning

    data = analysis_inputs()
    analysis = analyze_exam(**data)
    summary = summarize_learning(
        [analysis, analysis], student_id=analysis.student_id, curriculum=data["curriculum"]
    )
    assert summary.actual_exam_count == 1
    assert summary.selected_exam_ids == [analysis.exam_id]
    assert summary.requested_exam_count == 3
    assert summary.unresolved_question_count == 2
    assert summary.warnings
    assert all(point.interpretation == "practice_signal_only" for point in summary.points)


def prepared_synthetic_intake(tmp_path):
    from app.exam_intake import prepare_exam_intake
    from PIL import Image

    source_dir = tmp_path / "synthetic-originals"
    source_dir.mkdir()
    source = source_dir / "synthetic.png"
    image = Image.new("RGB", (3, 3), "white")
    image.putpixel((0, 0), (255, 0, 0))
    image.save(source)
    root = tmp_path / "synthetic-intake"
    intake = prepare_exam_intake(
        exam_id="synthetic-exam",
        student_id="synthetic-student",
        is_synthetic_student=True,
        expected_question_numbers=[1],
        paper_paths=[source],
        response_paths=[],
        reference_paths=[],
        output_dir=root,
    )
    return root, intake


def test_crop_transform_describes_actual_integer_pixel_extent_not_requested_float_extent(tmp_path):
    from app.exam_contracts import ExamRegion
    from app.exam_intake import crop_exam_regions
    from PIL import Image

    root, intake = prepared_synthetic_intake(tmp_path)
    region = ExamRegion(page_id=intake.pages[0].id, bbox=[0.2, 0.2, 0.8, 0.8], role="paper")
    cropped = crop_exam_regions(intake, [region], root)
    child = next(page for page in cropped.pages if page.parent_page_id)
    with Image.open(root / child.image_path) as image:
        assert image.size == (3, 3)
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert child.parent_bbox == (0, 0, 1, 1)


def test_changed_original_copy_invalidates_crop_reuse_even_when_png_page_is_unchanged(tmp_path):
    from app.exam_contracts import ExamRegion
    from app.exam_intake import ExamIntakeError, crop_exam_regions

    root, intake = prepared_synthetic_intake(tmp_path)
    source = root / intake.materials[0].source_path
    source.write_bytes(source.read_bytes() + b"synthetic-source-tamper")
    with pytest.raises(ExamIntakeError):
        crop_exam_regions(
            intake, [ExamRegion(page_id=intake.pages[0].id, bbox=[0, 0, 1, 1], role="paper")], root
        )


def test_crop_output_parent_symlink_cannot_publish_outside_intake_root(tmp_path):
    from app.exam_contracts import ExamRegion
    from app.exam_intake import ExamIntakeError, crop_exam_regions

    root, intake = prepared_synthetic_intake(tmp_path)
    outside = tmp_path / "synthetic-outside"
    outside.mkdir()
    (root / "pages/crops").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ExamIntakeError):
        crop_exam_regions(
            intake, [ExamRegion(page_id=intake.pages[0].id, bbox=[0, 0, 1, 1], role="paper")], root
        )
    assert not list(outside.iterdir())


def test_second_crop_publication_failure_restores_old_manifest_and_removes_first_asset(
    tmp_path, monkeypatch
):
    import app.exam_intake
    from app.exam_contracts import ExamRegion
    from app.exam_intake import ExamIntakeError, crop_exam_regions

    root, intake = prepared_synthetic_intake(tmp_path)
    before_manifest = (root / "intake.json").read_bytes()
    before_files = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    real_link = app.exam_intake.os.link
    calls = []

    def fail_second_link(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise OSError("synthetic publication failure")
        return real_link(*args, **kwargs)

    monkeypatch.setattr(app.exam_intake.os, "link", fail_second_link)
    regions = [
        ExamRegion(page_id=intake.pages[0].id, bbox=box, role="paper")
        for box in [[0, 0, 0.3, 1], [0.6, 0, 1, 1]]
    ]
    with pytest.raises(ExamIntakeError):
        crop_exam_regions(intake, regions, root)
    assert len(calls) == 2
    assert (root / "intake.json").read_bytes() == before_manifest
    assert {
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    } == before_files


def test_importing_identical_material_under_new_exam_id_does_not_create_a_second_exam():
    from app.exam_analysis import analyze_exam, summarize_learning

    first_inputs = analysis_inputs()
    alias_inputs = copy.deepcopy(first_inputs)
    alias_inputs["intake"].exam_id = "synthetic-import-alias"
    first = analyze_exam(**first_inputs)
    alias = analyze_exam(**alias_inputs)
    summary = summarize_learning(
        [first, alias], student_id=first.student_id, curriculum=first_inputs["curriculum"]
    )
    assert summary.actual_exam_count == 1
    assert len(summary.selected_exam_ids) == 1
    assert summary.unresolved_question_count == 2


def test_part_only_response_correction_is_not_lost_without_a_transcript_edit():
    from app.exam_analysis import analyze_exam, apply_exam_corrections
    from app.exam_contracts import ExamCorrection

    data = analysis_inputs()
    data["paper_results"][0].questions[0].part_paths = ["1", "2"]
    data["response_results"][0].responses[0].part_path = "1"
    original = analyze_exam(**data)
    correction = ExamCorrection(
        id="synthetic-part-edit",
        base_revision=original.revision,
        number=1,
        response_id="synthetic-response-1",
        corrected_part_path="2",
        evidence=[evidence("response-1", "handwriting")],
    )
    changed = apply_exam_corrections(
        original, [correction], intake=data["intake"], curriculum=data["curriculum"]
    )
    response = next(row for row in changed.student_responses if row.id == "synthetic-response-1")
    assert response.part_path == "2"
    assert response.transcript == "synthetic response 1"


def test_revision_change_rejects_stale_or_reused_teacher_correction_ids():
    from app.exam_analysis import analyze_exam, apply_exam_corrections
    from app.exam_contracts import ExamCorrection

    data = analysis_inputs()
    original = analyze_exam(**data)
    correction = ExamCorrection(
        id="synthetic-correction-once",
        base_revision=original.revision,
        number=1,
        outcome="incorrect",
        evidence=[evidence("response-1", "handwriting")],
    )
    changed = apply_exam_corrections(
        original, [correction], intake=data["intake"], curriculum=data["curriculum"]
    )
    with pytest.raises(ValueError):
        apply_exam_corrections(
            changed, [correction], intake=data["intake"], curriculum=data["curriculum"]
        )
    correction.base_revision = changed.revision
    with pytest.raises(ValueError):
        apply_exam_corrections(
            changed, [correction], intake=data["intake"], curriculum=data["curriculum"]
        )


def test_latest_three_distinct_exams_use_corrected_revision_once_with_source_evidence():
    from app.exam_analysis import analyze_exam, apply_exam_corrections, summarize_learning
    from app.exam_contracts import ExamCorrection

    records = []
    last_inputs = None
    for number in range(1, 5):
        data = analysis_inputs()
        raw = data["intake"].model_dump(mode="python")
        raw["exam_id"] = f"synthetic-exam-{number}"
        response_digest = f"{100 + number:064x}"
        raw["materials"][1]["sha256"] = response_digest
        raw["pages"][1]["document_sha256"] = response_digest
        data["intake"] = ExamIntake.model_validate(raw)
        data["exam_date"] = f"2026-01-0{number}"
        records.append(analyze_exam(**data))
        last_inputs = data
    assert last_inputs is not None
    old = records[-1]
    correction = ExamCorrection(
        id="synthetic-latest-teacher-correction",
        base_revision=old.revision,
        number=1,
        outcome="incorrect",
        point_ids=["p1"],
        evidence=[evidence("response-1", "handwriting")],
    )
    changed = apply_exam_corrections(
        old,
        [correction],
        intake=last_inputs["intake"],
        curriculum=last_inputs["curriculum"],
    )
    summary = summarize_learning(
        [*records, changed, changed],
        student_id=old.student_id,
        curriculum=last_inputs["curriculum"],
    )
    assert summary.actual_exam_count == 3
    assert summary.selected_exam_ids == ["synthetic-exam-4", "synthetic-exam-3", "synthetic-exam-2"]
    assert summary.unresolved_question_count == 5
    assert len(summary.points) == 1
    point = summary.points[0]
    assert (point.observed_question_count, point.error_question_count) == (1, 1)
    assert point.exam_ids == ["synthetic-exam-4"]
    assert point.evidence and point.interpretation == "practice_signal_only"


def test_editing_transcript_invalidates_old_supported_error_reason_and_part_diagnoses():
    from app.exam_analysis import analyze_exam, apply_exam_corrections
    from app.exam_contracts import ExamCorrection, ExamModelResponse

    data = analysis_inputs()
    data["response_results"][0].responses[0].grading = [
        GradingObservation(kind="deducted", value=10, literal="-10", sources=[evidence()])
    ]
    data["diagnosis_results"] = [
        ExamModelResponse(
            task="diagnosis",
            diagnoses=[
                {
                    "number": 1,
                    "outcome": "incorrect",
                    "point_ids": ["p1"],
                    "reason": "synthetic old explanation",
                    "reason_status": "supported",
                    "evidence": [evidence("response-1", "handwriting")],
                }
            ],
        )
    ]
    original = analyze_exam(**data)
    assert original.questions[0].reason == "synthetic old explanation"
    correction = ExamCorrection(
        id="synthetic-change-evidence",
        base_revision=original.revision,
        number=1,
        response_id="synthetic-response-1",
        corrected_transcript="synthetic corrected reading",
        evidence=[evidence("response-1", "handwriting")],
    )
    changed = apply_exam_corrections(
        original, [correction], intake=data["intake"], curriculum=data["curriculum"]
    )
    assert changed.questions[0].reason == ""
    assert changed.questions[0].reason_status == "unknown"
    assert changed.questions[0].part_diagnoses == []


def test_unmarked_response_without_reference_key_is_not_graded_by_model_self_assertion():
    from app.exam_analysis import analyze_exam
    from app.exam_contracts import ExamModelResponse

    data = analysis_inputs()
    data["diagnosis_results"] = [
        ExamModelResponse(
            task="diagnosis",
            diagnoses=[
                {
                    "number": 1,
                    "outcome": "incorrect",
                    "point_ids": ["p1"],
                    "reason": "synthetic model judgement, no teacher grade or reference supplied",
                    "reason_status": "supported",
                    "evidence": [evidence("response-1", "handwriting")],
                }
            ],
        )
    ]
    question = analyze_exam(**data).questions[0]
    assert question.outcome == "unknown"
    assert question.awarded is None and question.deducted is None
    assert question.reason_status != "supported"


def test_error_reason_cannot_cite_another_response_region_on_the_same_page():
    from app.exam_analysis import analyze_exam
    from app.exam_contracts import ExamModelResponse

    data = analysis_inputs()
    data["response_results"][0].responses[0].grading = [
        GradingObservation(kind="deducted", value=10, literal="-10", sources=[evidence()])
    ]
    wrong_region = data["response_results"][0].responses[1].sources[0]
    data["diagnosis_results"] = [
        ExamModelResponse(
            task="diagnosis",
            diagnoses=[
                {
                    "number": 1,
                    "outcome": "incorrect",
                    "point_ids": ["p1"],
                    "reason": "synthetic cause claimed from different question's region",
                    "reason_status": "supported",
                    "evidence": [wrong_region],
                }
            ],
        )
    ]
    question = analyze_exam(**data).questions[0]
    assert question.outcome == "incorrect"
    assert question.reason_status != "supported"
    assert all(diagnosis.reason_status != "supported" for diagnosis in question.part_diagnoses)


@pytest.mark.asyncio
async def test_codex_exam_result_symlink_is_rejected_before_outside_json_is_accepted(tmp_path):
    import json
    import sys

    from app.codex_cli import CodexCLIConfig
    from app.exam_models import CodexExamAdapter, ExamModelResponseError

    outside = tmp_path / "synthetic-external-result.json"
    outside.write_text(json.dumps({"task": "layout", "page_classification": "blank"}))
    fake_cli = tmp_path / "synthetic-codex"
    fake_cli.write_text(
        "#!" + sys.executable + "\n"
        "import os,sys\n"
        "target=sys.argv[sys.argv.index('--output-last-message')+1]\n"
        f"os.symlink({str(outside)!r},target)\n"
    )
    fake_cli.chmod(0o755)
    adapter = CodexExamAdapter(
        CodexCLIConfig(executable=str(fake_cli), model="synthetic-model", timeout_seconds=5)
    )
    with pytest.raises(ExamModelResponseError):
        await adapter.assess(task="layout", prompt="synthetic contract only", image_paths=[])


def test_same_version_curriculum_content_drift_rejects_corrections_and_learning_merge():
    from app.exam_analysis import analyze_exam, apply_exam_corrections, summarize_learning
    from app.exam_contracts import ExamCorrection

    data = analysis_inputs()
    analysis = analyze_exam(**data)
    changed_curriculum = data["curriculum"].model_copy(deep=True)
    changed_curriculum.points[0].name = "Synthetic changed meaning under the same version"
    assert changed_curriculum.version == analysis.curriculum_version
    correction = ExamCorrection(
        id="synthetic-curriculum-drift",
        base_revision=analysis.revision,
        number=1,
        outcome="incorrect",
        evidence=[evidence("response-1", "handwriting")],
    )
    with pytest.raises(ValueError):
        apply_exam_corrections(
            analysis, [correction], intake=data["intake"], curriculum=changed_curriculum
        )
    with pytest.raises(ValueError):
        summarize_learning(
            [analysis], student_id=analysis.student_id, curriculum=changed_curriculum
        )
