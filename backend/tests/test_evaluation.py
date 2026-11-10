import json
from pathlib import Path

from app.evaluation import EvaluationManifest, PredictionSet, evaluate

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures"


def test_fixture_reports_counts_and_keeps_synthetic_separate_from_real():
    manifest = EvaluationManifest.model_validate_json((FIXTURES / "manifest.json").read_text())
    predictions = PredictionSet.model_validate_json((FIXTURES / "predictions.json").read_text())
    report = evaluate(manifest, predictions)
    synthetic = report["synthetic"]
    assert synthetic["sample_count"] == 2
    assert synthetic["parse_coverage"] == {"numerator": 2, "denominator": 2, "rate": 1.0}
    assert synthetic["question_coverage"]["numerator"] == 3
    assert synthetic["question_coverage"]["denominator"] == 3
    assert synthetic["duplicate_questions"] == [
        {"sample_id": "synthetic-edge-002", "question_id": "q3", "count": 2}
    ]
    assert synthetic["extra_questions"] == [
        {"sample_id": "synthetic-edge-002", "question_id": "extra-q"}
    ]
    assert synthetic["association_recall"]["numerator"] == 2
    assert synthetic["association_recall"]["denominator"] == 3
    assert synthetic["unresolved_association_count"] == 1
    assert report["real"]["sample_count"] == 0
    assert report["real"]["parse_coverage"]["rate"] is None
    assert "not recognition quality" in report["provenance"]["synthetic"]


def test_missing_run_and_omitted_question_remain_in_manifest_denominators():
    manifest = EvaluationManifest.model_validate(
        {
            "samples": [
                {
                    "id": "real-1",
                    "kind": "real",
                    "submitted": True,
                    "expected_questions": [{"id": "q1"}, {"id": "q2"}],
                    "expected_associations": [{"id": "a1", "question_id": "q1"}],
                }
            ]
        }
    )
    report = evaluate(manifest, PredictionSet(runs=[]))["real"]
    assert report["parse_coverage"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert report["question_coverage"]["denominator"] == 2
    assert len(report["missing_questions"]) == 2
    assert report["association_recall"] == {"numerator": 0, "denominator": 1, "rate": 0.0}


def test_wrong_and_duplicate_associations_count_once_and_accuracy_denominator_is_predictions():
    manifest = EvaluationManifest.model_validate(
        {
            "samples": [
                {
                    "id": "s",
                    "kind": "synthetic",
                    "expected_questions": [{"id": "q"}],
                    "expected_associations": [
                        {"id": "right", "question_id": "q", "subquestion_id": "part-a"}
                    ],
                }
            ]
        }
    )
    predictions = PredictionSet(
        runs=[
            {
                "sample_id": "s",
                "parse_status": "parsed",
                "items": [
                    {"question_id": "q", "subquestion_id": "part-a", "association_id": "right"},
                    {"question_id": "q", "subquestion_id": "part-a", "association_id": "right"},
                    {"question_id": "q", "association_id": "wrong"},
                ],
            }
        ]
    )
    report = evaluate(manifest, predictions)["synthetic"]
    assert report["association_correctness_among_predictions"] == {
        "numerator": 1,
        "denominator": 3,
        "rate": 1 / 3,
    }
    assert report["association_recall"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert len(report["duplicate_associations"]) == 1
    assert len(report["wrong_associations"]) == 1
    assert report["ground_truth_adjudicated_complete_question_rate"]["rate"] == 0.0


def test_cli_fixture_runs_without_live_credentials(tmp_path):
    import subprocess
    import sys

    repo = FIXTURES.parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "evaluate.py"),
            "--output",
            str(tmp_path / "report.json"),
        ],
        cwd=repo,
        env={"PATH": str(Path(sys.executable).parent)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["real"]["sample_count"] == 0
