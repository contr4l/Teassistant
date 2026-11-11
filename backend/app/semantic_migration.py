"""Fail-closed compatibility migration from the v1 catalog to the v2 catalog."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from app.contracts import Curriculum, difficulty_level
from app.curriculum import CurriculumIndex
from app.semantic_bank import canonical_hash, validate_assessments, validate_input_manifest

_AFFECTED_SCHEMA = "semantic-affected-v1"
_CARRIED = "carried_forward_not_semantically_reassessed"
_DIRECT = "model_reassessment"
_RUN_COMPARE_KEYS = (
    "schema_version",
    "source_bank_sha256",
    "inputs_sha256",
    "difficulty_version",
    "prompt_version",
    "provider",
    "configured_model",
    "codex_executable",
    "mode",
    "curriculum_status",
    "practice_ready",
)
_FINAL_STATUSES = {"assessed", "failed", "not_attempted"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_bytes(path: Path) -> bytes:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"expected a regular file: {path.name}")
    return resolved.read_bytes()


def _load_json(path: Path) -> tuple[dict, bytes]:
    raw = _file_bytes(path)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return value, raw


def _same_assessment(left: object, right: object) -> bool:
    return canonical_hash(left) == canonical_hash(right)


def _row_identity(row: object, qid: str, entry: dict) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"annotation row is invalid: {qid}")
    if row.get("source_hash") != entry.get("source_hash"):
        raise ValueError(f"source hash mismatch: {qid}")
    if row.get("association_status") != entry.get("association_status"):
        raise ValueError(f"association status mismatch: {qid}")


def _validate_catalog_pair(
    old: Curriculum,
    new: Curriculum,
    old_index: CurriculumIndex,
    new_index: CurriculumIndex,
    old_raw: bytes,
    new_raw: bytes,
) -> None:
    old_document = json.loads(old_raw)
    new_document = json.loads(new_raw)
    for document, catalog in ((old_document, old), (new_document, new)):
        if (
            document.get("version") != catalog.version
            or type(document.get("is_synthetic")) is not bool
            or document["is_synthetic"] is not False
        ):
            raise ValueError("catalog metadata is not an exact non-synthetic draft")
    old_raw_points = old_document.get("points")
    new_raw_points = new_document.get("points")
    if not isinstance(old_raw_points, list) or not isinstance(new_raw_points, list):
        raise ValueError("catalog points must be JSON arrays")
    if len(new_raw_points) <= len(old_raw_points):
        raise ValueError("catalog v2 must append at least one new point")
    if new_raw_points[: len(old_raw_points)] != old_raw_points:
        raise ValueError("catalog v2 changed raw legacy point data")

    if old.is_synthetic or new.is_synthetic:
        raise ValueError("real-source migration requires non-synthetic curricula")
    if old.version == new.version:
        raise ValueError("catalog versions must differ")
    old_points = [point.model_dump(mode="json") for point in old.points]
    new_points = [point.model_dump(mode="json") for point in new.points]
    if len(new_points) <= len(old_points):
        raise ValueError("catalog v2 must append at least one new point")
    if new_points[: len(old_points)] != old_points:
        raise ValueError("catalog v2 changed an original point, name, order, or prerequisite list")
    if {point["id"] for point in old_points} & {
        point["id"] for point in new_points[len(old_points) :]
    }:
        raise ValueError("catalog v2 additions reuse an existing point id")
    for point in old_points:
        point_id = point["id"]
        if old_index.required([point_id]) != new_index.required([point_id]):
            raise ValueError(f"catalog v2 changes an original point closure: {point_id}")


def _validate_run_metadata(
    run: dict,
    curriculum: Curriculum,
    expected_ids: list[str],
    bank_sha256: str,
    inputs_sha256: str,
    catalog_sha256: str,
) -> None:
    if run.get("source_bank_sha256") != bank_sha256 or run.get("inputs_sha256") != inputs_sha256:
        raise ValueError("run source or input digest differs from frozen manifest")
    if run.get("curriculum_sha256") != catalog_sha256:
        raise ValueError("run curriculum digest differs from curriculum.json")
    if run.get("curriculum_version") != curriculum.version:
        raise ValueError("run curriculum version differs from curriculum.json")
    if (
        run.get("curriculum_status") != "engineering_draft"
        or run.get("practice_ready") is not False
    ):
        raise ValueError("semantic run must remain an unpublished engineering draft")
    question_ids = run.get("question_ids")
    if (
        not isinstance(question_ids, list)
        or any(not isinstance(qid, str) for qid in question_ids)
        or len(question_ids) != len(set(question_ids))
        or set(question_ids) != set(expected_ids)
    ):
        raise ValueError("run question IDs differ from fixed expected cohort")


def _run_file_bytes(run_dir: Path, relative: str) -> bytes:
    root = run_dir.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("run evidence file is missing or escapes run directory")
    return path.read_bytes()


def _load_run_json(run_dir: Path, relative: str) -> tuple[dict, bytes]:
    raw = _run_file_bytes(run_dir, relative)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid run JSON: {relative}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"run JSON root must be an object: {relative}")
    return value, raw


def _read_call(
    run_dir: Path,
    call_id: int,
    run_metadata: dict,
    allowed_ids: set[str],
    index: CurriculumIndex,
) -> dict:
    if type(call_id) is not int or call_id < 1:
        raise ValueError("call_id must be a positive integer")
    call_dir = run_dir / "calls" / f"{call_id:04d}"
    if not call_dir.is_dir():
        raise ValueError(f"missing call directory: {call_id}")
    if call_dir.is_symlink() or not call_dir.resolve(strict=True).is_relative_to(run_dir):
        raise ValueError("call directory escapes run directory")
    request, request_bytes = _load_run_json(run_dir, f"calls/{call_id:04d}/request.json")
    result, result_bytes = _load_run_json(run_dir, f"calls/{call_id:04d}/result.json")
    prompt_bytes = _run_file_bytes(run_dir, f"calls/{call_id:04d}/prompt.txt")
    prompt_sha256 = _sha256_bytes(prompt_bytes)
    qids = request.get("question_ids")
    if (
        not isinstance(qids, list)
        or not qids
        or any(not isinstance(qid, str) for qid in qids)
        or len(qids) != len(set(qids))
        or not set(qids) <= allowed_ids
    ):
        raise ValueError(f"invalid call question IDs: {call_id}")
    if request.get("prompt_sha256") != prompt_sha256:
        raise ValueError(f"prompt digest mismatch: {call_id}")
    result_qids = result.get("question_ids")
    if result_qids != qids:
        raise ValueError(f"request/result question IDs differ: {call_id}")
    configured_provider = run_metadata.get("provider")
    result_provider = result.get("provider")
    if configured_provider == "codex":
        allowed_providers = {"codex_cli"}
        if result.get("error_code") is not None:
            # The runner's error audit is written by its exception handler, before
            # an adapter result exists, so it records the selected CLI provider.
            allowed_providers.add("codex")
    else:
        allowed_providers = {configured_provider}
    if result_provider not in allowed_providers:
        raise ValueError(f"call provider differs from run configuration: {call_id}")
    if result.get("model") != run_metadata.get("configured_model"):
        raise ValueError(f"call model differs from run configuration: {call_id}")
    raw = result.get("raw_json_result")
    error_code = result.get("error_code")
    if error_code is None and not isinstance(raw, dict):
        raise ValueError(f"call result has neither a valid raw response nor an error: {call_id}")
    if error_code is not None:
        checked = None
    else:
        checked = validate_assessments(raw, qids, index)
    return {
        "call_id": call_id,
        "call_dir": call_dir,
        "question_ids": qids,
        "request": request,
        "request_sha256": _sha256_bytes(request_bytes),
        "prompt_sha256": prompt_sha256,
        "result": result,
        "result_sha256": _sha256_bytes(result_bytes),
        "raw": raw,
        "checked": checked,
        "error_code": error_code,
    }


def _sidecar_rows(sidecar: dict, run: dict, expected_ids: list[str]) -> dict:
    if sidecar.get("run") != run:
        raise ValueError("annotations sidecar run metadata differs from run.json")
    rows = sidecar.get("questions")
    if (
        not isinstance(rows, dict)
        or set(rows) != set(expected_ids)
        or len(rows) != len(expected_ids)
    ):
        raise ValueError("annotation rows differ from fixed expected cohort")
    return rows


def _audit_run(
    run_dir: Path,
    run_metadata: dict,
    sidecar: dict,
    expected_ids: list[str],
    entries: dict,
    index: CurriculumIndex,
) -> tuple[dict, dict]:
    rows = _sidecar_rows(sidecar, run_metadata, expected_ids)
    call_cache: dict[int, dict] = {}
    call_dirs = run_dir / "calls"
    if not call_dirs.is_dir():
        raise ValueError("run has no calls directory")
    if call_dirs.is_symlink() or not call_dirs.resolve(strict=True).is_relative_to(run_dir):
        raise ValueError("calls directory escapes run directory")

    # Validate each persisted adapter invocation, including retry records not used by the final row.
    invocation_records = []
    for path in sorted(call_dirs.iterdir()):
        if not path.is_dir():
            raise ValueError("calls directory contains a non-directory entry")
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(run_dir):
            raise ValueError("call directory escapes run directory")
        if not path.name.isdecimal():
            raise ValueError("call directory name must be numeric")
        call_id = int(path.name)
        if path.name != f"{call_id:04d}":
            raise ValueError("call directory must use its canonical zero-padded ID")
        if call_id in call_cache:
            raise ValueError("duplicate numeric call directory")
        # Error rows from an interrupted retry may lack a result; any referenced call is checked below.
        if not (path / "result.json").exists():
            invocation_records.append({"call_id": call_id, "incomplete": True, "attempts": 0})
            continue
        record = _read_call(run_dir, call_id, run_metadata, set(expected_ids), index)
        call_cache[call_id] = record
        attempts = record["result"].get("attempts", 1)
        if type(attempts) is not int or attempts < 1:
            raise ValueError(f"invalid adapter attempt count: {call_id}")
        invocation_records.append(
            {
                "call_id": call_id,
                "incomplete": False,
                "attempts": attempts,
            }
        )

    audited = {}
    for qid in expected_ids:
        row = rows[qid]
        _row_identity(row, qid, entries[qid])
        status = row.get("status")
        if status not in _FINAL_STATUSES:
            raise ValueError(f"run has unfinished or invalid row status: {qid}")
        call_id = row.get("call_id")
        if status == "not_attempted":
            if call_id is not None:
                raise ValueError(f"not-attempted row unexpectedly references a call: {qid}")
            if row.get("assessment") is not None or row.get("required_point_ids") != []:
                raise ValueError(f"not-attempted row contains assessment data: {qid}")
            audited[qid] = {"row": row, "status": status, "call": None, "checked": None}
            continue
        if type(call_id) is not int or call_id not in call_cache:
            raise ValueError(f"final row lacks a completed referenced call: {qid}")
        call = call_cache[call_id]
        if qid not in call["question_ids"]:
            raise ValueError(f"row call does not include question: {qid}")
        if call["error_code"] is not None:
            reconstructed = {
                question_id: {
                    "status": "failed",
                    "assessment": None,
                    "required_point_ids": [],
                    "error_code": call["error_code"],
                }
                for question_id in call["question_ids"]
            }
        else:
            reconstructed = call["checked"]
        for question_id in call["question_ids"]:
            sidecar_row = rows[question_id]
            if sidecar_row.get("call_id") != call_id:
                # This is an older retry for this row; the final row references a later call.
                continue
            expected = reconstructed[question_id]
            if (
                sidecar_row.get("status") != expected["status"]
                or sidecar_row.get("error_code") != expected["error_code"]
                or sidecar_row.get("required_point_ids") != expected["required_point_ids"]
                or not _same_assessment(sidecar_row.get("assessment"), expected["assessment"])
            ):
                raise ValueError(f"sidecar does not match validated raw response: {question_id}")
        checked = reconstructed[qid]
        audited[qid] = {"row": row, "status": status, "call": call, "checked": checked}

    return audited, {
        "adapter_invocations": sum(not call["incomplete"] for call in invocation_records),
        "incomplete_invocations": sum(call["incomplete"] for call in invocation_records),
        "client_calls": sum(call["attempts"] for call in invocation_records),
        "invocations": invocation_records,
    }


def _call_provenance(run_dir: Path, run: dict, call: dict | None, raw_item: dict | None) -> dict:
    if call is None:
        return {
            "source_run_path": str(run_dir),
            "call_id": None,
            "call_directory": None,
            "request_sha256": None,
            "prompt_sha256": None,
            "result_sha256": None,
            "response_item_sha256": None,
            "provider": run.get("provider"),
            "configured_model": run.get("configured_model"),
            "model": None,
            "model_metadata_source": None,
            "codex_executable": run.get("codex_executable"),
            "prompt_version": run.get("prompt_version"),
        }
    result = call["result"]
    return {
        "source_run_path": str(run_dir),
        "call_id": call["call_id"],
        "call_directory": f"calls/{call['call_id']:04d}",
        "request_sha256": call["request_sha256"],
        "prompt_sha256": call["prompt_sha256"],
        "result_sha256": call["result_sha256"],
        "response_item_sha256": canonical_hash(raw_item) if raw_item is not None else None,
        "provider": result.get("provider"),
        "configured_model": run.get("configured_model"),
        "model": result.get("model"),
        "model_metadata_source": result.get("model_metadata_source"),
        "codex_executable": run.get("codex_executable"),
        "prompt_version": run.get("prompt_version"),
    }


def _response_item(call: dict | None, qid: str) -> dict | None:
    if call is None or not isinstance(call.get("raw"), dict):
        return None
    assessments = call["raw"].get("assessments")
    if not isinstance(assessments, list):
        return None
    matches = [
        item for item in assessments if isinstance(item, dict) and item.get("question_id") == qid
    ]
    return matches[0] if len(matches) == 1 else None


def _make_output_row(
    qid: str,
    entry: dict,
    audited: dict,
    *,
    source_run_dir: Path,
    source_run: dict,
    source_catalog: Curriculum,
    source_catalog_sha256: str,
    validated_catalog: Curriculum,
    validated_catalog_sha256: str,
    validated_index: CurriculumIndex,
    method: str,
    reasons: list[str],
) -> dict:
    source_row = audited["row"]
    assessment = source_row.get("assessment")
    status = audited["status"]
    error_code = source_row.get("error_code")
    required = []
    if status == "assessed":
        if not isinstance(assessment, dict):
            raise ValueError(f"assessed row has no assessment: {qid}")
        required = sorted(
            validated_index.required(
                [*assessment.get("point_ids", []), *assessment.get("prerequisite_ids", [])]
            )
        )
    item = _response_item(audited.get("call"), qid)
    return {
        "status": status,
        "association_status": entry["association_status"],
        "source_hash": entry["source_hash"],
        "assessment": assessment,
        "required_point_ids": required,
        "error_code": error_code,
        "assessment_catalog_version": source_catalog.version,
        "assessment_catalog_sha256": source_catalog_sha256,
        "model_input_catalog_version": source_catalog.version,
        "model_input_catalog_sha256": source_catalog_sha256,
        "validated_catalog_version": validated_catalog.version,
        "validated_catalog_sha256": validated_catalog_sha256,
        "migration_method": method,
        "migration_reasons": reasons,
        "provenance": _call_provenance(source_run_dir, source_run, audited.get("call"), item),
    }


def _summary(
    final_rows: dict, base_audit: dict, v2_audit: dict, base_rows: dict, v2_rows: dict
) -> dict:
    final_counts = Counter(row["status"] for row in final_rows.values())
    methods = Counter(row["migration_method"] for row in final_rows.values())
    v1_counts = Counter(row["status"] for row in base_rows.values())
    v2_counts = Counter(row["status"] for row in v2_rows.values())
    distribution = Counter()
    for row in final_rows.values():
        assessment = row.get("assessment")
        if row["status"] == "assessed" and isinstance(assessment, dict):
            distribution[difficulty_level(assessment["difficulty_score"])] += 1
    return {
        "denominator": len(final_rows),
        "direct_reassessed": sum(
            row["status"] == "assessed" and row["migration_method"] == _DIRECT
            for row in final_rows.values()
        ),
        "carried_forward": sum(
            row["status"] == "assessed" and row["migration_method"] == _CARRIED
            for row in final_rows.values()
        ),
        "failed": final_counts["failed"],
        "not_attempted": final_counts["not_attempted"],
        "final_assessed": final_counts["assessed"],
        "migration_method_counts": dict(methods),
        "v1_status_counts": {
            key: v1_counts[key] for key in ("assessed", "failed", "not_attempted")
        },
        "v2_status_counts": {
            key: v2_counts[key] for key in ("assessed", "failed", "not_attempted")
        },
        "v1_adapter_invocations": base_audit["adapter_invocations"],
        "v1_incomplete_invocations": base_audit["incomplete_invocations"],
        "v1_client_calls": base_audit["client_calls"],
        "v2_adapter_invocations": v2_audit["adapter_invocations"],
        "v2_incomplete_invocations": v2_audit["incomplete_invocations"],
        "v2_client_calls": v2_audit["client_calls"],
        "difficulty_distribution": {
            key: distribution[key] for key in ("basic", "intermediate", "advanced")
        },
        "practice_ready": 0,
        "semantic_accuracy": "not_measured_against_independent_gold",
        "difficulty_calibration": "not_measured_against_teacher_or_student_data",
    }


def merge_semantic_runs(
    base_run: Path,
    reassessed_run: Path,
    bank_dir: Path,
    inputs_file: Path,
    affected_file: Path,
    output_dir: Path,
) -> dict:
    """Revalidate compatible V1 successes and merge only frozen V2 reassessments.

    The output directory must not exist. Neither source run, bank, nor input manifest is changed.
    """
    base_dir = Path(base_run).resolve(strict=True)
    v2_dir = Path(reassessed_run).resolve(strict=True)
    bank_root = Path(bank_dir).resolve(strict=True)
    if not base_dir.is_dir() or not v2_dir.is_dir() or not bank_root.is_dir():
        raise ValueError("run and bank inputs must be directories")
    out = Path(output_dir).expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError("migration output must be fresh")
    out_resolved = out.resolve(strict=False)
    protected = (base_dir, v2_dir, bank_root, Path(inputs_file).resolve(strict=True).parent)
    if any(
        root == out_resolved or root in out_resolved.parents or out_resolved in root.parents
        for root in protected
    ):
        raise ValueError("migration output must be separate from all immutable inputs")

    bank_file = (bank_root / "bank.json").resolve(strict=True)
    if not bank_file.is_relative_to(bank_root):
        raise ValueError("bank.json escapes source bank directory")
    bank_bytes = _file_bytes(bank_file)
    bank = json.loads(bank_bytes)
    bank_sha256 = _sha256_bytes(bank_bytes)
    input_path = Path(inputs_file).resolve(strict=True)
    inputs_bytes = _file_bytes(input_path)
    input_manifest = json.loads(inputs_bytes)
    inputs_sha256 = _sha256_bytes(inputs_bytes)
    entries = validate_input_manifest(bank, input_manifest, input_path.parent, bank_sha256)

    affected, affected_bytes = _load_json(Path(affected_file))
    if affected.get("schema_version") != _AFFECTED_SCHEMA:
        raise ValueError("unsupported affected-question manifest schema")
    expected_ids = affected.get("expected_question_ids")
    affected_ids = affected.get("affected_question_ids")
    reasons = affected.get("reasons")
    bank_ids = [item.get("id") for item in bank.get("questions", [])]
    if (
        not isinstance(expected_ids, list)
        or len(expected_ids) != 200
        or any(not isinstance(qid, str) for qid in expected_ids)
        or len(expected_ids) != len(set(expected_ids))
        or any(not isinstance(qid, str) for qid in bank_ids)
        or set(expected_ids) != set(bank_ids)
        or len(set(bank_ids)) != len(bank_ids)
    ):
        raise ValueError("affected manifest must freeze the fixed 200 unique source IDs")
    if (
        not isinstance(affected_ids, list)
        or any(not isinstance(qid, str) for qid in affected_ids)
        or len(affected_ids) != len(set(affected_ids))
        or not set(affected_ids) <= set(expected_ids)
    ):
        raise ValueError("affected question IDs must be unique members of the fixed cohort")
    if not isinstance(reasons, dict) or set(reasons) != set(affected_ids):
        raise ValueError("affected reasons must cover exactly the affected IDs")
    for qid, reason in reasons.items():
        if not isinstance(qid, str) or not (
            isinstance(reason, str)
            and reason.strip()
            or isinstance(reason, list)
            and reason
            and all(isinstance(item, str) and item.strip() for item in reason)
        ):
            raise ValueError("each affected question needs a non-empty reason")
    if (
        affected.get("source_bank_sha256") != bank_sha256
        or affected.get("inputs_sha256") != inputs_sha256
    ):
        raise ValueError("affected manifest source or input digest does not match")

    base_run_meta, base_run_bytes = _load_run_json(base_dir, "run.json")
    v2_run_meta, v2_run_bytes = _load_run_json(v2_dir, "run.json")
    base_annotations, base_annotations_bytes = _load_run_json(base_dir, "annotations.json")
    v2_annotations, _ = _load_run_json(v2_dir, "annotations.json")
    old_catalog_bytes = _run_file_bytes(base_dir, "curriculum.json")
    new_catalog_bytes = _run_file_bytes(v2_dir, "curriculum.json")
    old_catalog_hash = _sha256_bytes(old_catalog_bytes)
    new_catalog_hash = _sha256_bytes(new_catalog_bytes)
    if affected.get("base_run_sha256") != _sha256_bytes(base_run_bytes):
        raise ValueError("base run digest does not match frozen affected manifest")
    if affected.get("base_annotations_sha256") != _sha256_bytes(base_annotations_bytes):
        raise ValueError("base annotations digest does not match frozen affected manifest")
    if (
        affected.get("old_curriculum_sha256") != old_catalog_hash
        or affected.get("new_curriculum_sha256") != new_catalog_hash
    ):
        raise ValueError("catalog digest does not match frozen affected manifest")
    if (
        affected.get("source_bank_sha256") != bank_sha256
        or affected.get("inputs_sha256") != inputs_sha256
    ):
        raise ValueError("affected manifest uses different source data")

    old_catalog = Curriculum.model_validate_json(old_catalog_bytes)
    new_catalog = Curriculum.model_validate_json(new_catalog_bytes)
    old_index = CurriculumIndex(old_catalog)
    new_index = CurriculumIndex(new_catalog)
    _validate_catalog_pair(
        old_catalog,
        new_catalog,
        old_index,
        new_index,
        old_catalog_bytes,
        new_catalog_bytes,
    )
    _validate_run_metadata(
        base_run_meta, old_catalog, expected_ids, bank_sha256, inputs_sha256, old_catalog_hash
    )
    _validate_run_metadata(
        v2_run_meta, new_catalog, expected_ids, bank_sha256, inputs_sha256, new_catalog_hash
    )
    for key in _RUN_COMPARE_KEYS:
        if base_run_meta.get(key) != v2_run_meta.get(key):
            raise ValueError(f"run configuration differs between catalogs: {key}")
    if base_run_meta.get("difficulty_version") != v2_run_meta.get("difficulty_version"):
        raise ValueError("difficulty rubric changed; compatibility migration is disallowed")

    base_rows = _sidecar_rows(base_annotations, base_run_meta, expected_ids)
    v2_rows = _sidecar_rows(v2_annotations, v2_run_meta, expected_ids)
    failed_ids = {qid for qid, row in base_rows.items() if row.get("status") == "failed"}
    if not failed_ids <= set(affected_ids):
        raise ValueError("every original failed question must be included in affected manifest")
    for qid, row in v2_rows.items():
        if qid not in set(affected_ids) and row.get("status") != "not_attempted":
            raise ValueError("V2 run contains a result outside frozen affected scope")

    base_audited, base_call_counts = _audit_run(
        base_dir, base_run_meta, base_annotations, expected_ids, entries, old_index
    )
    v2_audited, v2_call_counts = _audit_run(
        v2_dir, v2_run_meta, v2_annotations, expected_ids, entries, new_index
    )
    for qid in affected_ids:
        if v2_audited[qid]["status"] != "not_attempted" and v2_audited[qid]["call"] is None:
            raise ValueError(f"affected question is missing V2 call provenance: {qid}")

    final_rows = {}
    for qid in expected_ids:
        if qid in set(affected_ids):
            source_audit = v2_audited[qid]
            source_catalog = new_catalog
            source_catalog_hash = new_catalog_hash
            source_run_dir = v2_dir
            source_run_meta = v2_run_meta
            method = _DIRECT
            reason = reasons[qid]
            reason_list = [reason] if isinstance(reason, str) else reason
        else:
            source_audit = base_audited[qid]
            source_catalog = old_catalog
            source_catalog_hash = old_catalog_hash
            source_run_dir = base_dir
            source_run_meta = base_run_meta
            method = _CARRIED
            reason_list = []
            if source_audit["status"] == "failed":
                raise ValueError(f"V1 failure cannot be carried forward: {qid}")
            if source_audit["status"] == "assessed":
                old_row = source_audit["row"]
                raw_item = _response_item(source_audit["call"], qid)
                if raw_item is None:
                    raise ValueError(f"carried assessment lacks raw response item: {qid}")
                v2_checked = validate_assessments({"assessments": [raw_item]}, [qid], new_index)[
                    qid
                ]
                if v2_checked["status"] != "assessed" or not _same_assessment(
                    old_row.get("assessment"), v2_checked["assessment"]
                ):
                    raise ValueError(f"V1 assessment is not valid under V2: {qid}")
        validated_row = _make_output_row(
            qid,
            entries[qid],
            source_audit,
            source_run_dir=source_run_dir,
            source_run=source_run_meta,
            source_catalog=source_catalog,
            source_catalog_sha256=source_catalog_hash,
            validated_catalog=new_catalog,
            validated_catalog_sha256=new_catalog_hash,
            validated_index=new_index,
            method=method,
            reasons=reason_list,
        )
        # Record assessment origin separately from the new catalog validation target.
        final_rows[qid] = validated_row

    summary = _summary(final_rows, base_call_counts, v2_call_counts, base_rows, v2_rows)
    if (
        summary["final_assessed"] + summary["failed"] + summary["not_attempted"]
        != summary["denominator"]
    ):
        raise ValueError("final migration status counts do not partition the fixed cohort")
    if summary["practice_ready"] != 0:
        raise ValueError("semantic migration output can never become practice-ready")

    metadata = {
        "schema_version": "source-semantic-migration-v1",
        "source_bank_sha256": bank_sha256,
        "inputs_sha256": inputs_sha256,
        "affected_manifest_sha256": _sha256_bytes(affected_bytes),
        "base_run_sha256": _sha256_bytes(base_run_bytes),
        "base_annotations_sha256": _sha256_bytes(base_annotations_bytes),
        "base_curriculum_sha256": old_catalog_hash,
        "reassessed_run_json_sha256": _sha256_bytes(v2_run_bytes),
        "validated_curriculum_sha256": new_catalog_hash,
        "curriculum_version": new_catalog.version,
        "practice_ready": False,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.migration-stage-", dir=out.parent))
    try:
        (stage / "annotations.json").write_text(
            json.dumps({"run": metadata, "questions": final_rows}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        (stage / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "migration.json").write_bytes(
            json.dumps(affected, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        )
        (stage / "curriculum.json").write_bytes(new_catalog_bytes)
        stage.rename(out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return summary
