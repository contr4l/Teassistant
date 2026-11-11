"""Synthetic fixed-cohort failure accounting; no real source materials."""

import copy
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def evaluator():
    path = Path(__file__).resolve().parents[2] / "scripts/evaluate_associations.py"
    spec = importlib.util.spec_from_file_location("evaluate_associations", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cohort(tmp_path, evaluator):
    (tmp_path / "sources").mkdir()
    sources, documents = [], []
    for role in ["original", "analysis"]:
        path = tmp_path / "sources" / f"synthetic-{role}.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", b"<synthetic/>")
            archive.writestr("word/_rels/document.xml.rels", b"<synthetic/>")
        signature = evaluator.digest(path.read_bytes())
        source = {
            "document_id": role,
            "filename": path.name,
            "sha256": signature,
            "bytes": path.stat().st_size,
            "document_xml_sha256": evaluator.digest(b"<synthetic/>"),
            "relationships_sha256": evaluator.digest(b"<synthetic/>"),
            "pair_id": "synthetic-pair",
            "role": role,
        }
        sources.append(source)
        documents.append(
            {"id": role, "sha256": signature, "pair_id": "synthetic-pair", "role": role}
        )
    questions, expected = [], []
    for number in range(1, 21):
        blocks = [{"text": f"{number}. 合成题x²", "html": f"<p>{number}. 合成题x²</p>"}]
        question = {
            "id": f"synthetic-q{number}",
            "pair_id": "synthetic-pair",
            "number": number,
            "source_question_range": [number, number],
            "analysis_question_range": [number, number],
            "stem_blocks": blocks,
            "analysis_stem_blocks": copy.deepcopy(blocks),
            "answer_blocks": [{"text": "合成答案", "html": "<p>合成答案</p>"}],
            "structure_status": "imported",
            "answer_available": True,
            "association_status": "confirmed",
        }
        questions.append(question)
        expected.append(
            {
                **{
                    key: question[key]
                    for key in [
                        "id",
                        "pair_id",
                        "number",
                        "source_question_range",
                        "analysis_question_range",
                    ]
                },
                "original_document_id": "original",
                "analysis_document_id": "analysis",
                "source_stem_snapshot_sha256": evaluator.snapshot(blocks),
                "analysis_stem_snapshot_sha256": evaluator.snapshot(blocks),
                "answer_snapshot_sha256": evaluator.snapshot(question["answer_blocks"]),
            }
        )
    bank = {
        "documents": documents,
        "pairs": [
            {
                "id": "synthetic-pair",
                "original_document_id": "original",
                "analysis_document_id": "analysis",
            }
        ],
        "questions": questions,
    }
    manifest = {
        "denominator": 20,
        "minimum_confirmed": 19,
        "minimum_rate": 0.95,
        "questions": expected,
        "sources": sources,
    }
    return tmp_path, bank, manifest


def run(evaluator, cohort):
    root, bank, manifest = cohort
    (root / "bank.json").write_text(json.dumps(bank))
    return evaluator.evaluate(root, manifest)


def test_fixed_cohort_accepts_exactly_95_percent_and_retains_failure(evaluator, cohort):
    # An imported association that cannot be confirmed stays in the denominator.
    question = cohort[1]["questions"][-1]
    question["analysis_stem_blocks"][0] = {"text": "20. 合成题x3", "html": "<p>20. 合成题x3</p>"}
    cohort[2]["questions"][-1]["analysis_stem_snapshot_sha256"] = evaluator.snapshot(
        question["analysis_stem_blocks"]
    )
    question["association_status"] = "unverified"
    result = run(evaluator, cohort)
    assert result["passed"] and result["confirmed"] == 19
    assert result["confirmed_rate"] == 0.95 and result["unverified"] == 1


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "duplicate", "source", "snapshot", "reported", "pair", "answer_flag"],
)
def test_high_success_count_cannot_hide_cohort_or_evidence_drift(evaluator, cohort, change):
    root, bank, _ = cohort
    if change == "missing":
        bank["questions"].pop()
    elif change == "extra":
        question = copy.deepcopy(bank["questions"][-1])
        question["id"] = "unexpected"
        bank["questions"].append(question)
    elif change == "duplicate":
        bank["questions"].append(copy.deepcopy(bank["questions"][-1]))
    elif change == "source":
        (root / "sources/synthetic-original.docx").write_bytes(b"changed input")
    elif change == "snapshot":
        bank["questions"][-1]["answer_blocks"][0]["text"] = "changed answer"
    elif change == "reported":
        bank["questions"][-1]["association_status"] = "unverified"
    elif change == "pair":
        bank["pairs"].append(copy.deepcopy(bank["pairs"][0]))
    else:
        bank["questions"][-1]["answer_available"] = "false"
    result = run(evaluator, cohort)
    assert result["denominator"] == 20
    assert not result["passed"] and not result["integrity_ok"]


def test_cohort_manifest_cannot_drop_expected_ids(evaluator, cohort):
    cohort[2]["questions"].pop()
    with pytest.raises(ValueError, match="cohort"):
        run(evaluator, cohort)
