"""Independent semantic annotation acceptance, using synthetic content only."""

import base64
import hashlib
import io
import json
from contextlib import contextmanager
from copy import deepcopy

import pytest
from app.semantic_contracts import SemanticAssessment
from lxml import html as lxml_html
from PIL import Image
from pydantic import ValidationError


def assessment_payload():
    return {
        "question_id": "synthetic-q1",
        "status": "assessed",
        "point_ids": ["equations", "triangles"],
        "prerequisite_ids": ["arithmetic"],
        "difficulty_score": 55,
        "reasoning": "Synthetic compound task requires both an equation and a triangle relation.",
        "point_evidence": [
            {"point_id": "equations", "role": "target", "reason": "Synthetic equation step."},
            {"point_id": "triangles", "role": "target", "reason": "Synthetic triangle step."},
            {"point_id": "arithmetic", "role": "prerequisite", "reason": "Synthetic arithmetic."},
        ],
        "warnings": [],
    }


def test_compound_targets_keep_each_role_specific_evidence():
    value = SemanticAssessment.model_validate(assessment_payload())
    assert value.point_ids == ["equations", "triangles"]
    assert value.prerequisite_ids == ["arithmetic"]
    assert len(value.point_evidence) == 3


@pytest.mark.parametrize("score", [-1, 101, True, False, 55.0, "55", None])
def test_assessed_score_rejects_boolean_coercion_and_invalid_values(score):
    payload = assessment_payload()
    payload["difficulty_score"] = score
    with pytest.raises(ValidationError):
        SemanticAssessment.model_validate(payload)


@pytest.mark.parametrize("score", [0, 39, 40, 69, 70, 100])
def test_exact_integer_score_boundaries_remain_usable(score):
    payload = assessment_payload()
    payload["difficulty_score"] = score
    assert SemanticAssessment.model_validate(payload).difficulty_score == score


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_target",
        "role_overlap",
        "missing_evidence",
        "duplicate_evidence",
        "wrong_evidence_role",
    ],
)
def test_compound_label_or_evidence_inconsistency_is_rejected(mutation):
    payload = assessment_payload()
    if mutation == "duplicate_target":
        payload["point_ids"].append("equations")
    elif mutation == "role_overlap":
        payload["prerequisite_ids"].append("triangles")
    elif mutation == "missing_evidence":
        payload["point_evidence"].pop(0)
    elif mutation == "duplicate_evidence":
        payload["point_evidence"].append(deepcopy(payload["point_evidence"][0]))
    else:
        payload["point_evidence"][0]["role"] = "prerequisite"
    with pytest.raises(ValidationError):
        SemanticAssessment.model_validate(payload)


def test_insufficient_evidence_has_no_fabricated_labels_or_score():
    payload = assessment_payload()
    payload.update(
        status="insufficient_evidence",
        point_ids=[],
        prerequisite_ids=[],
        point_evidence=[],
        difficulty_score=None,
        reasoning="The synthetic image is unreadable.",
    )
    result = SemanticAssessment.model_validate(payload)
    assert result.point_ids == result.prerequisite_ids == result.point_evidence == []
    assert result.difficulty_score is None
    for key, value in [("point_ids", ["equations"]), ("difficulty_score", 0)]:
        invalid = deepcopy(payload)
        invalid[key] = value
        with pytest.raises(ValidationError):
            SemanticAssessment.model_validate(invalid)


@pytest.mark.parametrize(
    "key",
    ["source_sha256", "curriculum_version", "difficulty_version", "ready", "provider", "run_mode"],
)
def test_model_cannot_claim_program_owned_metadata(key):
    payload = assessment_payload()
    payload[key] = "model-controlled"
    with pytest.raises(ValidationError):
        SemanticAssessment.model_validate(payload)


@pytest.mark.parametrize("location", ["reasoning", "point_reason"])
def test_whitespace_is_not_a_label_or_scoring_explanation(location):
    payload = assessment_payload()
    if location == "reasoning":
        payload["reasoning"] = " \n\t "
    else:
        payload["point_evidence"][0]["reason"] = " \n\t "
    with pytest.raises(ValidationError):
        SemanticAssessment.model_validate(payload)


def synthetic_bank(tmp_path, *, association="confirmed"):
    root = tmp_path / "bank"
    (root / "media").mkdir(parents=True)
    for name, color in (("stem", "black"), ("answer", "red"), ("analysis", "blue")):
        Image.new("RGB", (3, 3), color).save(root / "media" / f"{name}.png")

    def source_block(text, role):
        return {
            "text": text,
            "html": f'<p>{text}<img src="media/{role}.png" width="3" height="3"/></p>',
        }

    question = {
        "id": "synthetic-q1",
        "association_status": association,
        "structure_status": "imported",
        "practice_ready": False,
        "difficulty_score": None,
        "stem_blocks": [
            source_block("SYNTHETIC-STEM", "stem"),
            {"text": "AB", "html": "<table><tr><td>A</td><td>B</td></tr></table>"},
            {"text": "SYNTHETIC-LAST-BLOCK", "html": "<p>SYNTHETIC-LAST-BLOCK</p>"},
        ],
        "answer_blocks": [source_block("UNRELIABLE-ANSWER", "answer")],
        "analysis_blocks": [source_block("UNRELIABLE-ANALYSIS", "analysis")],
        "topics": [],
    }
    (root / "bank.json").write_text(json.dumps({"questions": [question]}))
    return root, question


@contextmanager
def fake_browser_page(chromium_executable):
    yield object()


@pytest.mark.parametrize("association", ["confirmed", "unverified"])
def test_rendered_inputs_keep_complete_source_and_exclude_unverified_solutions(
    tmp_path, monkeypatch, association
):
    from app import semantic_inputs

    source, _ = synthetic_bank(tmp_path, association=association)
    captured = []

    def fake_render(page, html_page, *, output_dir, qindex):
        captured.append(html_page)
        stream = io.BytesIO()
        Image.new("RGB", (5, 5), "white").save(stream, format="PNG")
        return semantic_inputs._tiles(stream.getvalue(), output_dir, qindex)

    monkeypatch.setattr(semantic_inputs, "_browser_page", fake_browser_page)
    monkeypatch.setattr(semantic_inputs, "_render_page", fake_render)
    manifest = semantic_inputs.render_inputs(source, tmp_path / "inputs", ["synthetic-q1"])
    assert len(manifest["questions"]) == 1
    row = manifest["questions"][0]
    assert row["question_id"] == "synthetic-q1"
    assert row["reliable_solution_used"] is (association == "confirmed")
    assert "SYNTHETIC-STEM" in captured[0] and "SYNTHETIC-LAST-BLOCK" in captured[0]
    assert "<table>" in captured[0]
    sources = lxml_html.fromstring(captured[0]).xpath("//img/@src")
    decoded_images = [base64.b64decode(src.split(",", 1)[1]) for src in sources]
    assert decoded_images[0] == (source / "media/stem.png").read_bytes()
    if association == "unverified":
        assert "UNRELIABLE-ANSWER" not in captured[0] and "UNRELIABLE-ANALYSIS" not in captured[0]
        assert len(decoded_images) == 1
        assert "UNRELIABLE-ANSWER" not in json.dumps(row["text"])
        assert "UNRELIABLE-ANALYSIS" not in json.dumps(row["text"])
    else:
        assert "UNRELIABLE-ANSWER" in captured[0] and "UNRELIABLE-ANALYSIS" in captured[0]
        assert len(decoded_images) == 3


def test_missing_source_image_fails_before_rendering_or_publishing(tmp_path, monkeypatch):
    from app import semantic_inputs

    source, _ = synthetic_bank(tmp_path)
    (source / "media/stem.png").unlink()

    def forbidden_browser(*args, **kwargs):
        pytest.fail("missing source image must be rejected before rendering or model work")

    monkeypatch.setattr(semantic_inputs, "_browser_page", fake_browser_page)
    monkeypatch.setattr(semantic_inputs, "_render_page", forbidden_browser)
    output = tmp_path / "inputs"
    with pytest.raises((ValueError, OSError)):
        semantic_inputs.render_inputs(source, output, ["synthetic-q1"])
    assert not output.exists()


def test_long_input_tiles_preserve_every_row_and_the_last_pixel(tmp_path):
    from app.semantic_inputs import _tiles

    original = Image.new("RGB", (3, 5300))
    for row in range(original.height):
        for column in range(original.width):
            original.putpixel((column, row), (row % 256, row // 256, column))
    stream = io.BytesIO()
    original.save(stream, format="PNG")
    target = tmp_path / "images"
    target.mkdir()
    records = _tiles(stream.getvalue(), target, 1)
    covered = set()
    for item in records:
        start, end = item["source_y_start"], item["source_y_end"]
        assert item["source_width"] == original.width and item["source_height"] == original.height
        with Image.open(tmp_path / item["path"]) as tile:
            assert tile.tobytes() == original.crop((0, start, original.width, end)).tobytes()
        covered.update(range(start, end))
    assert covered == set(range(original.height))
    assert records[0]["source_y_start"] == 0
    assert records[-1]["source_y_end"] == original.height


def synthetic_index():
    from app.contracts import Curriculum
    from app.curriculum import CurriculumIndex

    return CurriculumIndex(
        Curriculum.model_validate(
            {
                "version": "synthetic-only-v1",
                "is_synthetic": True,
                "points": [
                    {"id": "numbers", "name": "Synthetic numbers", "prerequisite_ids": []},
                    {
                        "id": "arithmetic",
                        "name": "Synthetic arithmetic",
                        "prerequisite_ids": ["numbers"],
                    },
                    {
                        "id": "equations",
                        "name": "Synthetic equations",
                        "prerequisite_ids": ["arithmetic"],
                    },
                    {
                        "id": "triangles",
                        "name": "Synthetic triangles",
                        "prerequisite_ids": ["arithmetic"],
                    },
                ],
            }
        )
    )


def test_program_derives_transitive_prerequisites_without_inventing_model_evidence():
    from app.semantic_bank import validate_assessments

    result = validate_assessments(
        {"assessments": [assessment_payload()]}, ["synthetic-q1"], synthetic_index()
    )
    row = result["synthetic-q1"]
    assert row["status"] == "assessed"
    assert set(row["required_point_ids"]) == {"numbers", "arithmetic", "equations", "triangles"}
    assert row["assessment"]["prerequisite_ids"] == ["arithmetic"]
    assert "numbers" not in {item["point_id"] for item in row["assessment"]["point_evidence"]}


@pytest.mark.parametrize(
    "invalid_id", ["unexpected-question", ["synthetic-q1"], {"id": "synthetic-q1"}, None, True]
)
def test_extra_or_invalid_question_id_fails_closed_without_crashing(invalid_id):
    from app.semantic_bank import validate_assessments

    bad = assessment_payload()
    bad["question_id"] = invalid_id
    rows = validate_assessments(
        {"assessments": [assessment_payload(), bad]},
        ["synthetic-q1", "synthetic-q2"],
        synthetic_index(),
    )
    assert set(rows) == {"synthetic-q1", "synthetic-q2"}
    assert all(row["status"] == "failed" for row in rows.values())


def test_missing_and_duplicate_predictions_remain_in_expected_denominator():
    from app.semantic_bank import validate_assessments

    first, second = assessment_payload(), assessment_payload()
    second["question_id"] = "synthetic-q2"
    missing = validate_assessments(
        {"assessments": [first]}, ["synthetic-q1", "synthetic-q2"], synthetic_index()
    )
    assert missing["synthetic-q1"]["status"] == "assessed"
    assert missing["synthetic-q2"]["status"] == "failed"
    duplicate = validate_assessments(
        {"assessments": [first, deepcopy(first), second]},
        ["synthetic-q1", "synthetic-q2"],
        synthetic_index(),
    )
    assert duplicate["synthetic-q1"]["status"] == "failed"
    assert duplicate["synthetic-q2"]["status"] == "assessed"


def test_schema_valid_unknown_curriculum_id_is_still_rejected():
    from app.semantic_bank import validate_assessments

    payload = assessment_payload()
    payload["point_ids"][0] = "unknown-point"
    payload["point_evidence"][0]["point_id"] = "unknown-point"
    row = validate_assessments({"assessments": [payload]}, ["synthetic-q1"], synthetic_index())[
        "synthetic-q1"
    ]
    assert row["status"] == "failed" and row["error_code"] == "unknown_point_id"


def test_cyclic_curriculum_is_rejected_before_any_assessment():
    from app.contracts import Curriculum
    from app.curriculum import CurriculumIndex

    catalog = Curriculum.model_validate(
        {
            "version": "synthetic-cycle",
            "points": [
                {"id": "a", "name": "Synthetic A", "prerequisite_ids": ["b"]},
                {"id": "b", "name": "Synthetic B", "prerequisite_ids": ["a"]},
            ],
        }
    )
    with pytest.raises(ValueError, match="cycle"):
        CurriculumIndex(catalog)


def input_cohort(tmp_path):
    from app.semantic_bank import canonical_hash

    path = tmp_path / "tile.png"
    Image.new("RGB", (5, 5), "white").save(path)
    bank = {
        "questions": [
            {"id": f"synthetic-q{n}", "association_status": "confirmed", "stem_blocks": []}
            for n in range(200)
        ]
    }
    inputs = {
        "source_bank_sha256": "synthetic-bank-hash",
        "questions": [
            {
                "question_id": q["id"],
                "source_hash": canonical_hash(q),
                "association_status": "confirmed",
                "reliable_solution_used": True,
                "text": "Synthetic source only.",
                "images": [
                    {"path": "tile.png", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                ],
            }
            for q in bank["questions"]
        ],
    }
    return bank, inputs


@pytest.mark.parametrize(
    "mutation",
    ["source_bank", "question_source", "image_bytes", "missing", "duplicate", "association"],
)
def test_input_cohort_or_source_changes_invalidate_semantic_processing(tmp_path, mutation):
    from app.semantic_bank import canonical_hash, validate_input_manifest

    bank, inputs = input_cohort(tmp_path)
    assert len(validate_input_manifest(bank, inputs, tmp_path, "synthetic-bank-hash")) == 200
    if mutation == "source_bank":
        inputs["source_bank_sha256"] = "different-bank"
    elif mutation == "question_source":
        inputs["questions"][0]["source_hash"] = "stale-source"
    elif mutation == "image_bytes":
        Image.new("RGB", (5, 5), "black").save(tmp_path / "tile.png")
    elif mutation == "missing":
        inputs["questions"].pop()
    elif mutation == "duplicate":
        inputs["questions"][-1] = deepcopy(inputs["questions"][0])
    else:
        bank["questions"][0]["association_status"] = "unverified"
        inputs["questions"][0].update(
            source_hash=canonical_hash(bank["questions"][0]),
            association_status="unverified",
            reliable_solution_used=True,
        )
    with pytest.raises((ValueError, OSError)):
        validate_input_manifest(bank, inputs, tmp_path, "synthetic-bank-hash")
