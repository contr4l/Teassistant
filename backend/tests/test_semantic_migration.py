from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.contracts import Curriculum
from app.curriculum import CurriculumIndex
from app.semantic_bank import canonical_hash
from app.semantic_migration import merge_semantic_runs
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def _save(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return raw


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assessment(qid: str, target: str) -> dict:
    return {
        "question_id": qid,
        "status": "assessed",
        "point_ids": [target],
        "prerequisite_ids": [],
        "difficulty_score": 41,
        "reasoning": "合成测试依据。",
        "point_evidence": [{"point_id": target, "role": "target", "reason": "合成测试证据。"}],
        "warnings": [],
    }


def _make_run(
    directory: Path,
    ids: list[str],
    entries: list[dict],
    bank_sha: str,
    input_sha: str,
    catalog_path: Path,
    *,
    selected: list[str],
    call_provider: str = "codex_cli",
    call_error: str | None = None,
) -> dict:
    directory.mkdir()
    catalog_raw = catalog_path.read_bytes()
    (directory / "curriculum.json").write_bytes(catalog_raw)
    curriculum = Curriculum.model_validate_json(catalog_raw)
    index = CurriculumIndex(curriculum)
    metadata = {
        "schema_version": "source-semantic-run-v1",
        "source_bank_sha256": bank_sha,
        "inputs_sha256": input_sha,
        "curriculum_sha256": hashlib.sha256(catalog_raw).hexdigest(),
        "curriculum_version": curriculum.version,
        "curriculum_status": "engineering_draft",
        "prompt_version": "source-semantic-prompt-v2",
        "difficulty_version": "junior-math-demand-v1",
        "provider": "codex",
        "configured_model": "synthetic-model",
        "codex_executable": {"path": "/synthetic/codex", "sha256": "a" * 64},
        "mode": "development_validation",
        "question_ids": ids,
        "practice_ready": False,
    }
    _save(directory / "run.json", metadata)
    rows = {
        entry["question_id"]: {
            "status": "not_attempted",
            "source_hash": entry["source_hash"],
            "association_status": entry["association_status"],
            "assessment": None,
            "required_point_ids": [],
            "error_code": None,
        }
        for entry in entries
    }
    if selected:
        call_id = 1
        call_dir = directory / "calls" / f"{call_id:04d}"
        call_dir.mkdir(parents=True)
        prompt = "Synthetic prompt."
        prompt_raw = prompt.encode()
        (call_dir / "prompt.txt").write_bytes(prompt_raw)
        _save(
            call_dir / "request.json",
            {
                "question_ids": selected,
                "prompt_sha256": hashlib.sha256(prompt_raw).hexdigest(),
                "started_at": "2026-11-11T00:00:00+00:00",
            },
        )
        target = (
            curriculum.points[-1].id if directory.name == "reassessed" else curriculum.points[0].id
        )
        raw_items = [_assessment(qid, target) for qid in selected]
        result = {
            "provider": call_provider,
            "model": "synthetic-model",
            "model_metadata_source": "configured_model",
            "attempts": 1,
            "usage": None,
            "raw_json_result": {"assessments": raw_items},
            "elapsed_seconds": 0.01,
            "question_ids": selected,
        }
        if call_error:
            result.update(error_code=call_error, raw_json_result=None)
        _save(call_dir / "result.json", result)
        for item in raw_items:
            qid = item["question_id"]
            if call_error:
                rows[qid].update(status="failed", error_code=call_error, call_id=call_id)
            else:
                rows[qid].update(
                    status="assessed",
                    assessment=item,
                    required_point_ids=sorted(index.required([target])),
                    error_code=None,
                    call_id=call_id,
                )
    _save(directory / "annotations.json", {"run": metadata, "questions": rows})
    return metadata


def _case(tmp_path: Path, *, patch_error: str | None = None):
    bank_dir = tmp_path / "bank"
    input_dir = tmp_path / "inputs"
    bank_dir.mkdir()
    input_dir.mkdir()
    question_ids = [f"synthetic-{index:03d}" for index in range(200)]
    questions = [
        {"id": qid, "association_status": "confirmed", "stem_blocks": [{"text": qid}]}
        for qid in question_ids
    ]
    bank_raw = _save(bank_dir / "bank.json", {"questions": questions})
    image_path = input_dir / "tile.png"
    Image.new("RGB", (4, 4), "white").save(image_path, format="PNG")
    image_hash = _hash(image_path)
    entries = [
        {
            "question_id": question["id"],
            "source_hash": canonical_hash(question),
            "association_status": "confirmed",
            "reliable_solution_used": True,
            "text": question["id"],
            "images": [{"path": "tile.png", "sha256": image_hash, "width": 4, "height": 4}],
        }
        for question in questions
    ]
    input_raw = _save(
        input_dir / "inputs.json",
        {"source_bank_sha256": hashlib.sha256(bank_raw).hexdigest(), "questions": entries},
    )
    old_catalog = ROOT / "data/curricula/junior-math-2022-draft-v1.json"
    new_catalog = ROOT / "data/curricula/junior-math-2022-draft-v2.json"
    affected_ids = question_ids[:2]
    base = tmp_path / "base"
    reassessed = tmp_path / "reassessed"
    _make_run(
        base,
        question_ids,
        entries,
        hashlib.sha256(bank_raw).hexdigest(),
        hashlib.sha256(input_raw).hexdigest(),
        old_catalog,
        selected=question_ids,
    )
    _make_run(
        reassessed,
        question_ids,
        entries,
        hashlib.sha256(bank_raw).hexdigest(),
        hashlib.sha256(input_raw).hexdigest(),
        new_catalog,
        selected=affected_ids,
        call_provider="codex" if patch_error else "codex_cli",
        call_error=patch_error,
    )
    affected = tmp_path / "affected.json"
    _save(
        affected,
        {
            "schema_version": "semantic-affected-v1",
            "source_bank_sha256": hashlib.sha256(bank_raw).hexdigest(),
            "inputs_sha256": hashlib.sha256(input_raw).hexdigest(),
            "base_run_sha256": _hash(base / "run.json"),
            "base_annotations_sha256": _hash(base / "annotations.json"),
            "old_curriculum_sha256": _hash(base / "curriculum.json"),
            "new_curriculum_sha256": _hash(reassessed / "curriculum.json"),
            "expected_question_ids": question_ids,
            "affected_question_ids": affected_ids,
            "reasons": {qid: ["synthetic fixture"] for qid in affected_ids},
        },
    )
    return {
        "base": base,
        "reassessed": reassessed,
        "bank": bank_dir,
        "inputs": input_dir / "inputs.json",
        "affected": affected,
        "output": tmp_path / "merged",
        "ids": question_ids,
    }


def _merge(case):
    return merge_semantic_runs(
        case["base"],
        case["reassessed"],
        case["bank"],
        case["inputs"],
        case["affected"],
        case["output"],
    )


def test_merge_keeps_old_assessments_and_marks_direct_reassessment(tmp_path):
    case = _case(tmp_path)
    frozen = {
        path: _hash(path)
        for directory in (case["base"], case["reassessed"], case["bank"])
        for path in directory.rglob("*")
        if path.is_file()
    }

    summary = _merge(case)

    merged = json.loads((case["output"] / "annotations.json").read_text())
    rows = merged["questions"]
    assert summary["denominator"] == 200
    assert summary["carried_forward"] == 198
    assert summary["direct_reassessed"] == 2
    assert summary["practice_ready"] == 0
    assert rows[case["ids"][2]]["migration_method"] == "carried_forward_not_semantically_reassessed"
    assert rows[case["ids"][0]]["migration_method"] == "model_reassessment"
    assert (case["output"] / "curriculum.json").read_bytes() == (
        case["reassessed"] / "curriculum.json"
    ).read_bytes()
    assert all(_hash(path) == digest for path, digest in frozen.items())


def test_failed_v2_response_does_not_fall_back_and_accepts_runner_error_provider(tmp_path):
    case = _case(tmp_path, patch_error="synthetic_failure")

    summary = _merge(case)

    rows = json.loads((case["output"] / "annotations.json").read_text())["questions"]
    assert rows[case["ids"][0]]["status"] == "failed"
    assert rows[case["ids"][0]]["assessment"] is None
    assert rows[case["ids"][0]]["error_code"] == "synthetic_failure"
    assert rows[case["ids"][0]]["migration_method"] == "model_reassessment"
    assert summary["failed"] == 2
    assert summary["carried_forward"] == 198


def test_tampered_sidecar_is_rejected_even_when_manifest_is_rehashed(tmp_path):
    case = _case(tmp_path)
    path = case["base"] / "annotations.json"
    data = json.loads(path.read_text())
    qid = case["ids"][4]
    data["questions"][qid]["assessment"]["difficulty_score"] = True
    _save(path, data)
    affected = json.loads(case["affected"].read_text())
    affected["base_annotations_sha256"] = _hash(path)
    _save(case["affected"], affected)

    with pytest.raises(ValueError, match="sidecar does not match"):
        _merge(case)
    assert not case["output"].exists()
