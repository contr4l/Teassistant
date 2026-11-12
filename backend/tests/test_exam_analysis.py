from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.contracts import Curriculum, KnowledgePoint
from app.exam_analysis import analyze_exam, apply_exam_corrections, summarize_learning
from app.exam_contracts import (
    DiagnosisProposal,
    ExamCorrection,
    ExamEvidence,
    ExamIntake,
    ExamMaterial,
    ExamModelResponse,
    ExamPage,
    GradingObservation,
    PaperQuestion,
    ReferenceAnswer,
    SectionScoreRule,
    StudentResponse,
)
from app.exam_view import _response_texts, render_exam_report
from PIL import Image


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _image(root, name, color):
    path = root / name
    Image.new("RGB", (320, 440), color).save(path, format="PNG")
    return path, _sha(path.read_bytes())


def _evidence(page_id, kind, bbox=(0.1, 0.1, 0.9, 0.9), excerpt=""):
    return ExamEvidence(page_id=page_id, kind=kind, bbox=bbox, excerpt=excerpt)


def _fixture(root, *, with_grade=False, with_reference=False, question_text="计算 1+1"):
    paper_bytes = b"paper source"
    response_bytes = b"response source"
    paper_sha = _sha(paper_bytes)
    response_sha = _sha(response_bytes)
    paper_image, paper_image_sha = _image(root, "paper.png", "white")
    response_image, response_image_sha = _image(root, "response.png", "#eeeeee")
    pages = [
        ExamPage(
            id="paper-page",
            document_sha256=paper_sha,
            source_page=1,
            role="paper",
            image_path=paper_image.name,
            image_sha256=paper_image_sha,
            width=320,
            height=440,
        ),
        ExamPage(
            id="response-page",
            document_sha256=response_sha,
            source_page=1,
            role="response",
            image_path=response_image.name,
            image_sha256=response_image_sha,
            width=320,
            height=440,
        ),
    ]
    materials = [
        ExamMaterial(
            id="paper-material",
            sha256=paper_sha,
            source_path="paper.pdf",
            role="paper",
            page_ids=["paper-page"],
        ),
        ExamMaterial(
            id="response-material",
            sha256=response_sha,
            source_path="response.pdf",
            role="response",
            page_ids=["response-page"],
        ),
    ]
    if with_reference:
        ref_bytes = b"reference source"
        ref_sha = _sha(ref_bytes)
        ref_image, ref_image_sha = _image(root, "reference.png", "#dddddd")
        pages.append(
            ExamPage(
                id="reference-page",
                document_sha256=ref_sha,
                source_page=1,
                role="reference",
                image_path=ref_image.name,
                image_sha256=ref_image_sha,
                width=320,
                height=440,
            )
        )
        materials.append(
            ExamMaterial(
                id="reference-material",
                sha256=ref_sha,
                source_path="key.pdf",
                role="reference",
                page_ids=["reference-page"],
            )
        )
    intake = ExamIntake(
        exam_id="exam-a",
        student_id="student-a",
        is_synthetic_student=True,
        expected_question_numbers=[1, 2],
        materials=materials,
        pages=pages,
    )
    q1 = PaperQuestion(
        number=1,
        text=question_text,
        part_paths=["1", "2"],
        question_type="free_response",
        max_score=10,
        score_sources=[_evidence("paper-page", "header", (0.1, 0.1, 0.9, 0.45), "第1题10分")],
        sources=[_evidence("paper-page", "printed_question", (0.1, 0.1, 0.9, 0.45))],
        point_ids=["arithmetic"],
    )
    q2 = PaperQuestion(
        number=2,
        text="计算 2+2",
        sources=[_evidence("paper-page", "printed_question", (0.1, 0.52, 0.9, 0.9))],
        point_ids=["arithmetic"],
    )
    responses = [
        StudentResponse(
            id="response-1",
            number=1,
            legibility="readable",
            transcript="2",
            part_path="",
            sources=[_evidence("response-page", "handwriting", (0.1, 0.1, 0.9, 0.45))],
            paper_match="matched",
            paper_sources=[_evidence("paper-page", "printed_question", (0.1, 0.1, 0.9, 0.45))],
            grading=(
                [
                    GradingObservation(
                        part_path="",
                        kind="maximum",
                        value=10,
                        literal="满分10分",
                        sources=[_evidence("response-page", "grading", (0.1, 0.1, 0.9, 0.45))],
                    ),
                    GradingObservation(
                        part_path="",
                        kind="deducted",
                        value=4,
                        literal="扣4分",
                        sources=[_evidence("response-page", "grading", (0.1, 0.1, 0.9, 0.45))],
                    ),
                    GradingObservation(
                        part_path="",
                        kind="mark",
                        mark="incorrect",
                        literal="×",
                        sources=[_evidence("response-page", "grading", (0.1, 0.1, 0.9, 0.45))],
                    ),
                ]
                if with_grade
                else []
            ),
        ),
        StudentResponse(
            id="response-2",
            number=2,
            legibility="readable",
            transcript="4",
            sources=[_evidence("response-page", "handwriting", (0.1, 0.52, 0.9, 0.9))],
            paper_match="matched",
            paper_sources=[_evidence("paper-page", "printed_question", (0.1, 0.52, 0.9, 0.9))],
        ),
    ]
    paper_results = [ExamModelResponse(task="paper", questions=[q1, q2])]
    response_results = [ExamModelResponse(task="response", responses=responses)]
    diagnosis = [
        DiagnosisProposal(
            number=1,
            part_path="",
            outcome="partial",
            point_ids=["arithmetic"],
            reason="计算步骤有误",
            reason_status="supported",
            evidence=[_evidence("response-page", "handwriting", (0.1, 0.1, 0.9, 0.45))],
        ),
        DiagnosisProposal(
            number=1,
            part_path="1",
            outcome="unknown",
            point_ids=["arithmetic"],
            reason="小问步骤不可辨",
            reason_status="supported",
            evidence=[_evidence("response-page", "handwriting", (0.1, 0.1, 0.9, 0.45))],
        ),
        DiagnosisProposal(
            number=1,
            part_path="2",
            outcome="unknown",
            point_ids=["arithmetic"],
            reason="未找到小问分值",
            reason_status="supported",
            evidence=[_evidence("response-page", "handwriting", (0.1, 0.1, 0.9, 0.45))],
        ),
    ]
    diagnosis_results = [ExamModelResponse(task="diagnosis", diagnoses=diagnosis)]
    reference_results = []
    if with_reference:
        reference_results = [
            ExamModelResponse(
                task="reference",
                reference_answers=[
                    ReferenceAnswer(
                        number=1,
                        text="2",
                        sources=[_evidence("reference-page", "reference", excerpt="答案2")],
                    )
                ],
            )
        ]
    curriculum = Curriculum(
        version="test-v1",
        points=[KnowledgePoint(id="arithmetic", name="数与运算")],
    )
    return {
        "intake": intake,
        "paper_results": paper_results,
        "response_results": response_results,
        "diagnosis_results": diagnosis_results,
        "reference_results": reference_results,
        "curriculum": curriculum,
        "exam_date": None,
        "asset_root": root,
    }


def _run(data):
    return analyze_exam(
        intake=data["intake"],
        paper_results=data["paper_results"],
        response_results=data["response_results"],
        diagnosis_results=data["diagnosis_results"],
        reference_results=data["reference_results"],
        curriculum=data["curriculum"],
        exam_date=data["exam_date"],
    )


def test_unmarked_model_diagnosis_remains_hypothesis_and_never_sets_final_outcome(tmp_path):
    data = _fixture(tmp_path)
    analysis = _run(data)
    question = analysis.questions[0]
    assert question.association.status == "linked"
    assert question.outcome == "unknown"
    root_proposal = next(item for item in question.part_diagnoses if item.part_path == "")
    assert root_proposal.outcome == "partial"
    assert root_proposal.reason_status == "hypothesis"
    assert question.reason_status == "hypothesis"
    assert question.awarded is None
    assert analysis.total_awarded is None


def test_only_root_grade_scores_parent_and_keeps_ungraded_children_unknown(tmp_path):
    data = _fixture(tmp_path, with_grade=True)
    analysis = _run(data)
    question = analysis.questions[0]
    assert (question.maximum, question.deducted, question.awarded) == (10, 4, 6)
    assert question.outcome == "partial"
    assert {item.part_path: item.outcome for item in question.part_diagnoses} == {
        "": "partial",
        "1": "unknown",
        "2": "unknown",
    }
    assert analysis.questions[1].outcome == "unknown"


def test_per_question_rule_can_be_proven_by_adjacent_header_fragments(tmp_path):
    data = _fixture(tmp_path)
    first = _evidence("paper-page", "header", (0.706, 0.04, 0.833, 0.059), "第1题每")
    second = _evidence("paper-page", "header", (0.171, 0.063, 0.258, 0.082), "小题8分")
    rule = SectionScoreRule(
        scope="per_question",
        literal="第1题每小题8分",
        question_numbers=[1],
        max_score=8,
        sources=[first, second],
    )
    paper_result = data["paper_results"][0]
    question = paper_result.questions[0].model_copy(
        update={"max_score": None, "score_sources": []}, deep=True
    )
    data["paper_results"] = [
        paper_result.model_copy(
            update={
                "questions": [question, paper_result.questions[1]],
                "scoring_rules": [rule],
            },
            deep=True,
        )
    ]

    analysis = _run(data)

    assert analysis.questions[0].maximum == 8
    assert not any("per-question scope is not proven" in warning for warning in analysis.warnings)


def test_split_header_fragments_far_apart_do_not_prove_a_score_rule(tmp_path):
    data = _fixture(tmp_path)
    first = _evidence("paper-page", "header", (0.706, 0.04, 0.833, 0.059), "第1题每")
    distant = _evidence("paper-page", "header", (0.171, 0.30, 0.258, 0.319), "小题8分")
    rule = SectionScoreRule(
        scope="per_question",
        literal="第1题每小题8分",
        question_numbers=[1],
        max_score=8,
        sources=[first, distant],
    )
    paper_result = data["paper_results"][0]
    question = paper_result.questions[0].model_copy(
        update={"max_score": None, "score_sources": []}, deep=True
    )
    data["paper_results"] = [
        paper_result.model_copy(
            update={
                "questions": [question, paper_result.questions[1]],
                "scoring_rules": [rule],
            },
            deep=True,
        )
    ]

    analysis = _run(data)

    assert analysis.questions[0].maximum is None
    assert any("per-question scope is not proven" in warning for warning in analysis.warnings)
    assert analysis.questions[1].maximum is None


def test_response_to_paper_binding_requires_bbox_overlap_and_diagnosis_handwriting_binding(
    tmp_path,
):
    data = _fixture(tmp_path, with_grade=True)
    response = data["response_results"][0].responses[0]
    response.paper_sources = [_evidence("paper-page", "printed_question", (0.1, 0.52, 0.9, 0.9))]
    analysis = _run(data)
    assert analysis.questions[0].association.status == "ambiguous"
    assert analysis.questions[0].outcome == "unknown"

    data = _fixture(tmp_path, with_grade=True)
    data["diagnosis_results"][0].diagnoses[0].evidence = [
        _evidence("response-page", "handwriting", (0.1, 0.6, 0.9, 0.8))
    ]
    analysis = _run(data)
    question = analysis.questions[0]
    assert question.outcome == "partial"  # direct score is still valid
    assert "without the matched response's handwriting region" in " ".join(question.warnings)
    assert question.reason_status == "hypothesis"


def test_total_score_observation_is_not_backfilled_into_unmarked_questions(tmp_path):
    data = _fixture(tmp_path)
    data["response_results"][0].grading = [
        GradingObservation(
            kind="total_awarded",
            value=18,
            literal="总分18",
            sources=[_evidence("response-page", "grading")],
        ),
        GradingObservation(
            kind="total_maximum",
            value=20,
            literal="满分20",
            sources=[_evidence("response-page", "grading")],
        ),
    ]
    analysis = _run(data)
    assert {item.kind for item in analysis.observed_totals} == {
        "total_awarded",
        "total_maximum",
    }
    assert analysis.questions[0].awarded is None
    assert analysis.questions[1].awarded is None
    assert analysis.total_awarded is None


def test_corrections_append_revision_preserve_prior_and_apply_all_response_edits(tmp_path):
    data = _fixture(tmp_path, with_grade=True)
    original = _run(data)
    prior = original.model_dump(mode="json")
    corrections = [
        ExamCorrection(
            id="edit-q1",
            base_revision=original.revision,
            number=1,
            response_id="response-1",
            corrected_transcript="2（修正）",
            evidence=[
                _evidence("paper-page", "printed_question"),
                _evidence("response-page", "handwriting", (0.1, 0.1, 0.9, 0.45)),
            ],
        ),
        ExamCorrection(
            id="edit-q2",
            base_revision=original.revision,
            number=2,
            response_id="response-2",
            corrected_transcript="4（已核）",
            evidence=[
                _evidence("paper-page", "printed_question", (0.1, 0.52, 0.9, 0.9)),
                _evidence("response-page", "handwriting", (0.1, 0.52, 0.9, 0.9)),
            ],
        ),
    ]
    changed = apply_exam_corrections(
        original,
        corrections,
        intake=data["intake"],
        curriculum=data["curriculum"],
    )
    assert changed.parent_revision == original.revision
    assert changed.revision != original.revision
    transcripts = {response.id: response.transcript for response in changed.student_responses}
    assert transcripts == {"response-1": "2（修正）", "response-2": "4（已核）"}
    assert changed.questions[0].part_diagnoses == []
    assert changed.questions[0].reason == ""
    assert original.model_dump(mode="json") == prior


def test_learning_summary_deduplicates_same_material_and_uses_unknown_date_only_if_needed(tmp_path):
    data = _fixture(tmp_path, with_grade=True)
    analysis = _run(data)
    duplicate_import = analysis.model_copy(
        update={"exam_id": "duplicate-import", "exam_date": "2026-11-10"}, deep=True
    )
    dated = analysis.model_copy(
        update={"exam_id": "exam-b", "exam_date": "2026-11-09", "source_material_sha256": "f" * 64},
        deep=True,
    )
    summary = summarize_learning(
        [analysis, duplicate_import, dated],
        student_id=analysis.student_id,
        curriculum=data["curriculum"],
        limit=3,
    )
    assert summary.actual_exam_count == 2
    assert set(summary.selected_exam_ids) == {"duplicate-import", "exam-b"}
    assert summary.points[0].error_question_count == 2
    assert summary.points[0].interpretation == "practice_signal_only"


def test_report_escapes_model_text_and_copies_only_hash_checked_originals(tmp_path):
    data = _fixture(tmp_path, with_grade=True, question_text="<script>alert(1)</script>")
    response_stage = data["response_results"][0]
    first_response = response_stage.responses[0].model_copy(
        update={
            "part_path": "1",
            "legibility": "partial",
            "alternatives": ["另一种识别候选"],
        },
        deep=True,
    )
    data["response_results"] = [
        response_stage.model_copy(
            update={"responses": [first_response, response_stage.responses[1]]}, deep=True
        )
    ]
    analysis = _run(data)
    output = tmp_path.parent / f"{tmp_path.name}-report"
    report = render_exam_report(
        analysis,
        intake=data["intake"],
        asset_root=data["asset_root"],
        output_dir=output,
        curriculum=data["curriculum"],
    )
    html = Path(report["html_path"]).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert len(report["asset_paths"]) == 2
    assert "数与运算" in html
    assert "arithmetic" not in html
    assert "部分可辨，请以原图为准" in html
    assert "其他AI转录候选：另一种识别候选" in html
    assert "识别结果未关联到此小问，不能据此认定未作答或计零分：2" in html
    assert all(
        (output / "assets").resolve() in Path(path).resolve().parents
        for path in report["asset_paths"]
    )
    assert "paper.pdf" not in Path(report["json_path"]).read_text(encoding="utf-8")

    root_data = _fixture(tmp_path, question_text="计算 1+1")
    root_analysis = _run(root_data)
    root_report = render_exam_report(
        root_analysis,
        intake=root_data["intake"],
        asset_root=root_data["asset_root"],
        output_dir=tmp_path.parent / f"{tmp_path.name}-root-response-report",
        curriculum=root_data["curriculum"],
    )
    root_html = Path(root_report["html_path"]).read_text(encoding="utf-8")
    assert "有整题作答记录，但未按小问拆分；不能据此判断各小问是否缺答" in root_html


def test_report_rejects_same_version_curriculum_content_drift(tmp_path):
    data = _fixture(tmp_path)
    analysis = _run(data)
    changed = Curriculum(
        version=data["curriculum"].version,
        points=[KnowledgePoint(id="arithmetic", name="篡改后的考点名")],
    )
    with pytest.raises(ValueError, match="curriculum content differs"):
        render_exam_report(
            analysis,
            intake=data["intake"],
            asset_root=data["asset_root"],
            output_dir=tmp_path.parent / f"{tmp_path.name}-drift-report",
            curriculum=changed,
        )


def test_subquestion_coverage_reports_only_missing_leaf_paths(tmp_path):
    data = _fixture(tmp_path)
    source_responses = data["response_results"][0].responses
    responses = [
        source_responses[0].model_copy(update={"number": 25, "part_path": "1/1"}, deep=True),
        source_responses[1].model_copy(update={"number": 25, "part_path": "1/2"}, deep=True),
    ]
    question = SimpleNamespace(
        association=SimpleNamespace(
            status="linked", response_ids=[response.id for response in responses]
        )
    )
    paper = SimpleNamespace(part_paths=["1", "1/1", "1/2", "1/3"])

    text = "\n".join(
        _response_texts(question, {response.id: [response] for response in responses}, paper)
    )

    coverage = next(line for line in text.splitlines() if line.startswith("小问覆盖："))
    assert coverage == "小问覆盖：识别结果未关联到此小问，不能据此认定未作答或计零分：1/3"


def test_report_rejects_asset_hash_drift_and_path_escape(tmp_path):
    data = _fixture(tmp_path)
    analysis = _run(data)
    page = data["intake"].pages[0]
    page.image_path = "../escape.png"
    with pytest.raises(ValueError, match="escape the asset root"):
        render_exam_report(
            analysis,
            intake=data["intake"],
            asset_root=tmp_path,
            output_dir=tmp_path.parent / f"{tmp_path.name}-unsafe-report",
        )
