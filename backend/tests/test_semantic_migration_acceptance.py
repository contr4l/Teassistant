"""Independent migration checks: synthetic 200-question runs, no service calls."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.contracts import Curriculum
from app.curriculum import CurriculumIndex
from app.semantic_bank import build_semantic_prompt, canonical_hash
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_assessment(qid, target):
    return {
        "question_id": qid,
        "status": "assessed",
        "point_ids": [target],
        "prerequisite_ids": [],
        "difficulty_score": 20,
        "reasoning": "Synthetic task only.",
        "point_evidence": [
            {"point_id": target, "role": "target", "reason": "Synthetic evidence only."}
        ],
        "warnings": [],
    }


def prepare_case(tmp_path, *, patch_failure=False):
    bank_dir, input_dir = tmp_path / "bank", tmp_path / "inputs"
    bank_dir.mkdir()
    input_dir.mkdir()
    Image.new("RGB", (3, 3), "white").save(input_dir / "tile.png")
    ids = [f"synthetic-q{n:03d}" for n in range(200)]
    questions = [
        {
            "id": qid,
            "association_status": "confirmed",
            "practice_ready": False,
            "difficulty_score": None,
            "stem_blocks": [{"text": "Synthetic question.", "html": "<p>Synthetic question.</p>"}],
        }
        for qid in ids
    ]
    save(
        bank_dir / "bank.json",
        {"schema_version": "source-bank-v3", "is_synthetic": True, "questions": questions},
    )
    entries = [
        {
            "question_id": q["id"],
            "source_hash": canonical_hash(q),
            "association_status": "confirmed",
            "reliable_solution_used": True,
            "text": "Synthetic question.",
            "images": [
                {
                    "path": "tile.png",
                    "sha256": file_hash(input_dir / "tile.png"),
                    "width": 3,
                    "height": 3,
                }
            ],
        }
        for q in questions
    ]
    save(
        input_dir / "inputs.json",
        {"source_bank_sha256": file_hash(bank_dir / "bank.json"), "questions": entries},
    )
    executable = tmp_path / "synthetic-codex"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)

    def make_run(directory, catalog_file, selected, *, failed_ids=()):
        directory.mkdir()
        (directory / "curriculum.json").write_bytes(catalog_file.read_bytes())
        curriculum = Curriculum.model_validate_json(catalog_file.read_bytes())
        index = CurriculumIndex(curriculum)
        metadata = {
            "schema_version": "source-semantic-run-v1",
            "source_bank_sha256": file_hash(bank_dir / "bank.json"),
            "inputs_sha256": file_hash(input_dir / "inputs.json"),
            "curriculum_sha256": file_hash(catalog_file),
            "curriculum_version": curriculum.version,
            "curriculum_status": "engineering_draft",
            "prompt_version": "source-semantic-prompt-v2",
            "difficulty_version": "junior-math-demand-v1",
            "provider": "codex",
            "configured_model": "synthetic-model",
            "codex_executable": {"path": str(executable), "sha256": file_hash(executable)},
            "mode": "development_validation",
            "question_ids": ids,
            "practice_ready": False,
        }
        save(directory / "run.json", metadata)
        rows = {
            e["question_id"]: {
                "status": "not_attempted",
                "source_hash": e["source_hash"],
                "association_status": "confirmed",
                "assessment": None,
                "required_point_ids": [],
                "error_code": None,
            }
            for e in entries
        }
        groups = (
            [[qid] for qid in selected]
            if failed_ids
            else [selected[i : i + 4] for i in range(0, len(selected), 4)]
        )
        for number, qids in enumerate(groups, 1):
            call_dir = directory / "calls" / f"{number:04d}"
            call_dir.mkdir(parents=True)
            request_entries = [e for e in entries if e["question_id"] in qids]
            prompt = build_semantic_prompt(request_entries, index)
            (call_dir / "prompt.txt").write_text(prompt)
            save(
                call_dir / "request.json",
                {
                    "question_ids": qids,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "started_at": "2026-11-11T00:00:00+00:00",
                },
            )
            target = (
                "JM.NA.INTEGER_DIVISIBILITY"
                if directory.name == "patch"
                else "JM.NA.RATIONAL_CONCEPT"
            )
            raw = [synthetic_assessment(qid, target) for qid in qids]
            audit = {
                "provider": "codex_cli",
                "model": "synthetic-model",
                "model_metadata_source": "configured_model",
                "attempts": 1,
                "usage": None,
                "raw_json_result": {"assessments": raw},
                "elapsed_seconds": 0.01,
                "question_ids": qids,
            }
            for assessment in raw:
                qid = assessment["question_id"]
                if qid in failed_ids:
                    audit.update(error_code="synthetic_failure", raw_json_result=None)
                    rows[qid].update(
                        status="failed", error_code="synthetic_failure", call_id=number
                    )
                else:
                    rows[qid].update(
                        status="assessed",
                        assessment=assessment,
                        required_point_ids=sorted(index.required([target])),
                        error_code=None,
                        call_id=number,
                    )
            save(call_dir / "result.json", audit)
        save(directory / "annotations.json", {"run": metadata, "questions": rows})
        return metadata

    base, patch = tmp_path / "base", tmp_path / "patch"
    old = ROOT / "data/curricula/junior-math-2022-draft-v1.json"
    new = ROOT / "data/curricula/junior-math-2022-draft-v2.json"
    make_run(base, old, ids)
    make_run(patch, new, ids[:2], failed_ids=ids[:1] if patch_failure else ())
    affected = tmp_path / "affected.json"
    save(
        affected,
        {
            "schema_version": "semantic-affected-v1",
            "source_bank_sha256": file_hash(bank_dir / "bank.json"),
            "inputs_sha256": file_hash(input_dir / "inputs.json"),
            "base_run_sha256": file_hash(base / "run.json"),
            "base_annotations_sha256": file_hash(base / "annotations.json"),
            "old_curriculum_sha256": file_hash(old),
            "new_curriculum_sha256": file_hash(new),
            "expected_question_ids": ids,
            "affected_question_ids": ids[:2],
            "reasons": {qid: ["Synthetic affected candidate."] for qid in ids[:2]},
        },
    )
    return SimpleNamespace(
        base=base,
        patch=patch,
        bank=bank_dir,
        inputs=input_dir / "inputs.json",
        affected=affected,
        output=tmp_path / "merged",
        ids=ids,
    )


def merge(case):
    from app.semantic_migration import merge_semantic_runs

    return merge_semantic_runs(
        case.base, case.patch, case.bank, case.inputs, case.affected, case.output
    )


def test_explicit_migration_keeps_original_assessment_and_separates_versions(tmp_path):
    case = prepare_case(tmp_path)
    original = {
        p: file_hash(p)
        for d in (case.base, case.patch, case.bank)
        for p in d.rglob("*")
        if p.is_file()
    }
    summary = merge(case)
    rows = json.loads((case.output / "annotations.json").read_text())["questions"]
    assert len(rows) == len(set(rows)) == 200
    assert summary["denominator"] == 200 and summary["carried_forward"] == 198
    assert summary["direct_reassessed"] == 2 and summary["failed"] == summary["not_attempted"] == 0
    old_rows = json.loads((case.base / "annotations.json").read_text())["questions"]
    carried, direct = rows[case.ids[2]], rows[case.ids[0]]
    assert carried["assessment"] == old_rows[case.ids[2]]["assessment"]
    assert carried["assessment_catalog_version"] == "junior-math-2022-draft-v1"
    assert carried["model_input_catalog_sha256"] == file_hash(case.base / "curriculum.json")
    assert carried["validated_catalog_version"] == "junior-math-2022-draft-v2"
    assert carried["migration_method"] == "carried_forward_not_semantically_reassessed"
    assert direct["assessment_catalog_version"] == "junior-math-2022-draft-v2"
    assert direct["migration_method"] == "model_reassessment"
    assert direct["assessment"]["point_ids"] == ["JM.NA.INTEGER_DIVISIBILITY"]
    provenance = carried["provenance"]
    assert provenance["source_run_path"] == str(case.base.resolve())
    assert provenance["request_sha256"] == file_hash(case.base / "calls/0001/request.json")
    assert provenance["result_sha256"] == file_hash(case.base / "calls/0001/result.json")
    assert all(file_hash(path) == digest for path, digest in original.items())


def test_affected_failed_reassessment_cannot_fall_back_to_old_success(tmp_path):
    case = prepare_case(tmp_path, patch_failure=True)
    summary = merge(case)
    rows = json.loads((case.output / "annotations.json").read_text())["questions"]
    assert rows[case.ids[0]]["status"] == "failed"
    assert rows[case.ids[0]].get("assessment") is None
    assert summary["failed"] == 1 and summary["direct_reassessed"] == 1
    assert summary["carried_forward"] == 198


@pytest.mark.parametrize("tamper", ["sidecar", "raw_response"])
def test_raw_response_and_saved_assessment_disagreement_rejects_carry(tmp_path, tamper):
    case = prepare_case(tmp_path)
    if tamper == "sidecar":
        path = case.base / "annotations.json"
        data = json.loads(path.read_text())
        data["questions"][case.ids[2]]["assessment"]["difficulty_score"] = 99
        save(path, data)
        manifest = json.loads(case.affected.read_text())
        manifest["base_annotations_sha256"] = file_hash(path)
        save(case.affected, manifest)
    else:
        path = case.base / "calls/0001/result.json"
        data = json.loads(path.read_text())
        data["raw_json_result"]["assessments"][2]["difficulty_score"] = 99
        save(path, data)
    with pytest.raises((ValueError, OSError)):
        merge(case)


@pytest.mark.parametrize(
    "tamper",
    ["duplicate_expected", "extra_affected", "wrong_base_hash", "source_bank", "input_image"],
)
def test_affected_manifest_identity_changes_are_rejected(tmp_path, tamper):
    case = prepare_case(tmp_path)
    data = json.loads(case.affected.read_text())
    if tamper == "duplicate_expected":
        data["expected_question_ids"][-1] = data["expected_question_ids"][0]
    elif tamper == "extra_affected":
        data["affected_question_ids"].append("unexpected-question")
    elif tamper == "wrong_base_hash":
        data["base_run_sha256"] = "0" * 64
    elif tamper == "source_bank":
        bank = json.loads((case.bank / "bank.json").read_text())
        bank["questions"][0]["stem_blocks"][0]["text"] = "Changed synthetic source."
        save(case.bank / "bank.json", bank)
    else:
        Image.new("RGB", (3, 3), "black").save(case.inputs.parent / "tile.png")
    save(case.affected, data)
    with pytest.raises((ValueError, OSError)):
        merge(case)


@pytest.mark.parametrize("tamper", ["old_name", "old_name_whitespace", "old_prerequisite"])
def test_changing_old_catalog_point_is_not_an_additive_migration(tmp_path, tamper):
    case = prepare_case(tmp_path)
    path = case.patch / "curriculum.json"
    catalog = json.loads(path.read_text())
    if tamper == "old_name":
        catalog["points"][0]["name"] = "Changed synthetic meaning"
    elif tamper == "old_name_whitespace":
        catalog["points"][0]["name"] = " " + catalog["points"][0]["name"] + " "
    else:
        catalog["points"][0]["prerequisite_ids"] = ["JM.GE.SEGMENT_ANGLE"]
    save(path, catalog)
    metadata = json.loads((case.patch / "run.json").read_text())
    metadata["curriculum_sha256"] = file_hash(path)
    save(case.patch / "run.json", metadata)
    annotations = json.loads((case.patch / "annotations.json").read_text())
    annotations["run"] = metadata
    save(case.patch / "annotations.json", annotations)
    manifest = json.loads(case.affected.read_text())
    manifest["new_curriculum_sha256"] = file_hash(path)
    save(case.affected, manifest)
    with pytest.raises((ValueError, OSError)):
        merge(case)
