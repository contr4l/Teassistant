"""Package a source bank with byte-bound semantic evidence for local deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bank_practice import (  # noqa: E402
    _resolve_source_run_path,
    _safe_path,
    load_practice_bank,
)
from app.semantic_bank import canonical_hash  # noqa: E402


class PackageError(RuntimeError):
    """A safe packaging failure; never includes private source paths or content."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _copy_file(source: Path, destination: Path, relative: str, files: list[dict[str, str]]) -> str:
    source_stat = source.lstat()
    if not stat.S_ISREG(source_stat.st_mode) or source.is_symlink():
        raise PackageError("package input contains a non-regular file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            writer.write(chunk)
            digest.update(chunk)
    value = digest.hexdigest()
    files.append({"path": relative, "sha256": value})
    return value


def _copy_tree(source: Path, destination: Path, files: list[dict[str, str]]) -> None:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise PackageError("package input is not a directory")
    destination.mkdir(parents=True, exist_ok=False)
    for current, directory_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in directory_names:
            item = current_path / name
            if item.is_symlink() or not item.is_dir():
                raise PackageError("package input contains an unsafe directory entry")
            (destination / item.relative_to(source)).mkdir(parents=True, exist_ok=True)
        for name in file_names:
            item = current_path / name
            if item.is_symlink() or not item.is_file():
                raise PackageError("package input contains an unsafe file entry")
            relative = item.relative_to(source).as_posix()
            _copy_file(item, destination / relative, f"source/{relative}", files)


def _safe_json(path: Path) -> tuple[Any, bytes]:
    if path.is_symlink() or not path.is_file():
        raise PackageError("required package input is unavailable")
    raw = path.read_bytes()
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageError("required package input is invalid") from None


def _write_json(path: Path, value: Any) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return raw


def _inventory(bank) -> dict[str, Any]:
    return {
        "question_count": len(bank.questions),
        "eligible_count": sum(question.status == "eligible" for question in bank.questions),
        "blocked_count": sum(question.status == "blocked" for question in bank.questions),
        "questions": {
            question.id: {"status": question.status, "block_reasons": question.block_reasons}
            for question in bank.questions
        },
    }


_SEMANTIC_EVIDENCE_ERRORS = {
    "semantic_source_run_mismatch",
    "semantic_call_digest_mismatch",
    "semantic_call_cohort_mismatch",
    "semantic_call_raw_result_invalid",
    "semantic_call_unavailable",
    "semantic_response_item_missing",
    "semantic_response_item_mismatch",
}
_SOURCE_INTEGRITY_ERRORS = {
    "source_document_invalid",
    "source_document_copy_missing",
    "source_document_hash_mismatch",
    "source_media_missing_or_mismatched",
    "source_ole_missing_or_invalid",
}


def _validate_and_copy_evidence(
    *,
    annotations: dict[str, Any],
    semantic_root: Path,
    evidence_root: Path,
    source_bank_sha256: str,
    files: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    mappings: dict[str, dict[str, Any]] = {}
    new_paths: dict[str, str] = {}

    questions = annotations.get("questions")
    if not isinstance(questions, dict):
        raise PackageError("semantic annotation set is invalid")
    for question_id, row in questions.items():
        if not isinstance(row, dict):
            continue
        provenance = row.get("provenance")
        if not isinstance(provenance, dict) or "source_run_path" not in provenance:
            continue
        original_value = provenance.get("source_run_path")
        try:
            run_root = _resolve_source_run_path(semantic_root, original_value)
        except (OSError, TypeError, ValueError):
            raise PackageError("semantic run path is unsafe or unavailable") from None
        source_path_hash = _sha256(str(run_root).encode("utf-8"))
        mapping = mappings.get(str(run_root))
        if mapping is None:
            package_relative = f"evidence/runs/{source_path_hash[:24]}"
            target_root = evidence_root.parent / package_relative
            target_root.mkdir(parents=True, exist_ok=False)
            run_file = _safe_path(run_root, "run.json")
            run_raw = run_file.read_bytes()
            try:
                run_data = json.loads(run_raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise PackageError("semantic run evidence is invalid") from None
            if (
                not isinstance(run_data, dict)
                or run_data.get("source_bank_sha256") != source_bank_sha256
                or question_id not in run_data.get("question_ids", [])
            ):
                raise PackageError("semantic run evidence does not bind to this source bank")
            run_digest = _copy_file(
                run_file,
                target_root / "run.json",
                f"{package_relative}/run.json",
                files,
            )
            mapping = {
                "source_run_path_sha256": source_path_hash,
                "package_path": package_relative,
                "run_json_sha256": run_digest,
                "calls": [],
                "_calls_by_id": {},
            }
            mappings[str(run_root)] = mapping
        else:
            run_data, _ = _safe_json(run_root / "run.json")
            if question_id not in run_data.get("question_ids", []):
                raise PackageError("semantic run evidence does not include an annotated question")

        call_id = provenance.get("call_id")
        call_directory = provenance.get("call_directory")
        if type(call_id) is not int or call_id < 1 or call_directory != f"calls/{call_id:04d}":
            raise PackageError("semantic call reference is invalid")
        call_record = mapping["_calls_by_id"].get(call_id)
        if call_record is None:
            target_call = evidence_root.parent / mapping["package_path"] / call_directory
            target_call.mkdir(parents=True, exist_ok=False)
            call_files: dict[str, str] = {}
            for filename, digest_key in (
                ("request.json", "request_sha256"),
                ("prompt.txt", "prompt_sha256"),
                ("result.json", "result_sha256"),
            ):
                source_file = _safe_path(run_root, f"{call_directory}/{filename}")
                expected_digest = provenance.get(digest_key)
                actual_digest = _sha256(source_file.read_bytes())
                if not isinstance(expected_digest, str) or actual_digest != expected_digest:
                    raise PackageError("semantic call evidence digest mismatch")
                call_files[filename] = _copy_file(
                    source_file,
                    target_call / filename,
                    f"{mapping['package_path']}/{call_directory}/{filename}",
                    files,
                )
            request, request_raw = _safe_json(run_root / f"{call_directory}/request.json")
            result, _ = _safe_json(run_root / f"{call_directory}/result.json")
            prompt_raw = _safe_path(run_root, f"{call_directory}/prompt.txt").read_bytes()
            question_ids = request.get("question_ids") if isinstance(request, dict) else None
            result_ids = result.get("question_ids") if isinstance(result, dict) else None
            raw_response = result.get("raw_json_result") if isinstance(result, dict) else None
            assessments = (
                raw_response.get("assessments") if isinstance(raw_response, dict) else None
            )
            if (
                not isinstance(question_ids, list)
                or not question_ids
                or any(not isinstance(item, str) for item in question_ids)
                or len(question_ids) != len(set(question_ids))
                or not isinstance(run_data.get("question_ids"), list)
                or any(not isinstance(item, str) for item in run_data["question_ids"])
                or not set(question_ids) <= set(run_data.get("question_ids", []))
                or request.get("prompt_sha256") != _sha256(prompt_raw)
                or result_ids != question_ids
                or not isinstance(assessments, list)
            ):
                raise PackageError("semantic call evidence is inconsistent")
            indexed: dict[str, dict[str, Any]] = {}
            for assessment in assessments:
                if (
                    not isinstance(assessment, dict)
                    or not isinstance(assessment.get("question_id"), str)
                    or assessment["question_id"] not in question_ids
                    or assessment["question_id"] in indexed
                ):
                    raise PackageError("semantic call response evidence is invalid")
                indexed[assessment["question_id"]] = assessment
            call_record = {
                "call_directory": call_directory,
                "files": call_files,
                "_items": indexed,
            }
            mapping["_calls_by_id"][call_id] = call_record
            mapping["calls"].append({"call_directory": call_directory, "files": call_files})
        item = call_record["_items"].get(question_id)
        if (
            item is None
            or row.get("assessment") != item
            or provenance.get("response_item_sha256") != canonical_hash(item)
        ):
            raise PackageError("semantic response item does not match annotation")
        new_paths[str(run_root)] = mapping["package_path"]

    public_mappings = []
    for mapping in mappings.values():
        mapping.pop("_calls_by_id", None)
        mapping["calls"].sort(key=lambda item: item["call_directory"])
        public_mappings.append(mapping)
    public_mappings.sort(key=lambda item: item["package_path"])
    return public_mappings, new_paths


def _ensure_output_outside_inputs(output: Path, inputs: list[Path]) -> Path:
    target = output.expanduser()
    parent = target.parent.resolve(strict=False)
    candidate = parent / target.name
    for root in inputs:
        try:
            candidate.relative_to(root.resolve(strict=True))
        except ValueError:
            continue
        raise PackageError("output directory must be outside input directories")
    if candidate.exists() or candidate.is_symlink():
        raise PackageError("output directory already exists")
    return candidate


def package_question_bank(bank_dir: Path, semantic_dir: Path, output_dir: Path) -> dict[str, Any]:
    bank_root = Path(bank_dir).expanduser().resolve(strict=True)
    semantic_root = Path(semantic_dir).expanduser().resolve(strict=True)
    if not bank_root.is_dir() or not semantic_root.is_dir():
        raise PackageError("input directories are required")
    annotations_data, _ = _safe_json(semantic_root / "annotations.json")
    annotation_questions = (
        annotations_data.get("questions") if isinstance(annotations_data, dict) else None
    )
    if not isinstance(annotation_questions, dict):
        raise PackageError("semantic annotation set is invalid")
    referenced_run_roots: list[Path] = []
    for row in annotation_questions.values():
        if not isinstance(row, dict):
            continue
        provenance = row.get("provenance")
        source_run_path = (
            provenance.get("source_run_path") if isinstance(provenance, dict) else None
        )
        if row.get("status") == "assessed" and not source_run_path:
            raise PackageError("assessed annotation lacks source evidence")
        if source_run_path is not None:
            try:
                referenced_run_roots.append(
                    _resolve_source_run_path(semantic_root, source_run_path)
                )
            except (OSError, TypeError, ValueError):
                raise PackageError("semantic run path is unsafe or unavailable") from None
    output = _ensure_output_outside_inputs(
        Path(output_dir), [bank_root, semantic_root, *referenced_run_roots]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise PackageError("output directory already exists")

    before = load_practice_bank(bank_root, semantic_root)
    before_inventory = _inventory(before)
    for question in before.questions:
        if set(question.block_reasons) & _SOURCE_INTEGRITY_ERRORS:
            raise PackageError("source bank contains failed native provenance")
        if set(question.block_reasons) & _SEMANTIC_EVIDENCE_ERRORS:
            raise PackageError("semantic evidence failed its source integrity checks")

    source_manifest_raw = (bank_root / "bank.json").read_bytes()
    curriculum_raw = (semantic_root / "curriculum.json").read_bytes()
    annotations_raw = (semantic_root / "annotations.json").read_bytes()
    try:
        annotations = json.loads(annotations_raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageError("semantic annotations are invalid") from None
    if not isinstance(annotations, dict):
        raise PackageError("semantic annotations are invalid")

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        files: list[dict[str, str]] = []
        _copy_tree(bank_root, staging / "source", files)
        _copy_file(
            semantic_root / "curriculum.json",
            staging / "semantic/curriculum.json",
            "semantic/curriculum.json",
            files,
        )
        mappings, new_paths = _validate_and_copy_evidence(
            annotations=annotations,
            semantic_root=semantic_root,
            evidence_root=staging / "evidence",
            source_bank_sha256=_sha256(source_manifest_raw),
            files=files,
        )
        for row in annotations.get("questions", {}).values():
            if not isinstance(row, dict):
                continue
            provenance = row.get("provenance")
            if isinstance(provenance, dict):
                original_run = provenance.get("source_run_path")
                if isinstance(original_run, str):
                    resolved_run = _resolve_source_run_path(semantic_root, original_run)
                    packaged_path = new_paths.get(str(resolved_run))
                    if packaged_path is not None:
                        provenance["source_run_path"] = packaged_path
        packaged_annotations_raw = _write_json(staging / "semantic/annotations.json", annotations)
        files.append(
            {
                "path": "semantic/annotations.json",
                "sha256": _sha256(packaged_annotations_raw),
            }
        )
        source_copy_sha = (staging / "source/bank.json").read_bytes()
        if source_copy_sha != source_manifest_raw:
            raise PackageError("source bank copy changed during packaging")

        after = load_practice_bank(staging / "source", staging / "semantic")
        after_inventory = _inventory(after)
        if before_inventory["questions"] != after_inventory["questions"]:
            raise PackageError("packaged bank eligibility differs from source")
        manifest = {
            "schema_version": "question-bank-package-v1",
            "source_bank_sha256": _sha256(source_manifest_raw),
            "curriculum_sha256": _sha256(curriculum_raw),
            "annotations_source_sha256": _sha256(annotations_raw),
            "annotations_packaged_sha256": _sha256(packaged_annotations_raw),
            "source_run_mappings": mappings,
            "files": sorted(files, key=lambda item: item["path"]),
            "inventory_before": before_inventory,
            "inventory_after": after_inventory,
        }
        _write_json(staging / "manifest.json", manifest)

        # Cooperative exclusive lock prevents two package commands from replacing
        # the same target. The destination is still required to be absent.
        lock_path = output.with_name(f".{output.name}.publish-lock")
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise PackageError("another package publish is in progress") from None
        try:
            os.close(lock_fd)
            if output.exists() or output.is_symlink():
                raise PackageError("output directory already exists")
            os.rename(staging, output)
        finally:
            lock_path.unlink(missing_ok=True)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-dir", type=Path, required=True)
    parser.add_argument("--semantic-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = package_question_bank(args.bank_dir, args.semantic_dir, args.output_dir)
    except PackageError as exc:
        print(f"package failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print("package failed: invalid or unavailable input", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "question_count": manifest["inventory_after"]["question_count"],
                "eligible_count": manifest["inventory_after"]["eligible_count"],
                "blocked_count": manifest["inventory_after"]["blocked_count"],
                "manifest_sha256": _sha256((Path(args.output_dir) / "manifest.json").read_bytes()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
