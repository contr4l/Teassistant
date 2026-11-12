#!/usr/bin/env python3
"""Read-only, source-bound formula coverage scan for a source-bank snapshot.

The scan keeps question, formula-object, and formula-occurrence denominators
separate. It writes diagnostics only to a fresh output directory and never
stores source text, MathML, LaTeX, images, or native equation bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bank_practice import load_practice_bank  # noqa: E402

SOURCE_FIELDS = ("stem_blocks", "analysis_stem_blocks", "answer_blocks", "analysis_blocks")
CONVERTER_FIELDS = ("stem_blocks", "answer_blocks", "analysis_blocks")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_read(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _check_output_path(output: Path, bank: Path, semantic: Path) -> Path:
    if output.exists() or output.is_symlink():
        raise ValueError("output directory must be new")
    parent = output.parent.resolve(strict=True)
    destination = parent / output.name
    for source in (bank, semantic):
        if _is_within(destination, source) or _is_within(source, destination):
            raise ValueError("output and inputs must not overlap")
    return destination


def _load_converter(source: Path | None):
    if source is None:
        import app.math_latex as module

        return module, None
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix != ".py":
        raise ValueError("converter source must be a Python file")
    name = f"diagnostic_converter_{_sha256(source.read_bytes())[:16]}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ValueError("converter source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for attribute in (
        "_validate_source_binding",
        "_formula_occurrences",
        "_extract_occurrence_bytes",
        "_sha",
        "convert_ole_formula",
        "convert_omml_formula",
    ):
        if not callable(getattr(module, attribute, None)):
            raise ValueError(f"converter source lacks required interface: {attribute}")
    return module, source


def _convert_occurrence_once(module, question, docs, cache):
    """Validate each occurrence while parsing each raw formula SHA only once."""
    original_ole = module.convert_ole_formula
    original_omml = module.convert_omml_formula

    def ole(raw: bytes):
        key = ("ole", _sha256(raw))
        if key not in cache:
            cache[key] = original_ole(raw)
        return cache[key]

    def omml(xml: str):
        key = ("omml", _sha256(xml.encode("utf-8")))
        if key not in cache:
            cache[key] = original_omml(xml)
        return cache[key]

    module.convert_ole_formula = ole
    module.convert_omml_formula = omml
    try:
        return module._formula_occurrences(question, docs)
    finally:
        module.convert_ole_formula = original_ole
        module.convert_omml_formula = original_omml


def _formula_fields(question: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for field in SOURCE_FIELDS:
        total = 0
        blocks = question.get(field, [])
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                for kind in ("ole_occurrences", "omml_occurrences"):
                    values = block.get(kind, [])
                    total += len(values) if isinstance(values, list) else 0
        counts[field] = total
    return counts


def _formula_kind_counts(question: dict[str, Any]) -> dict[str, Counter[str]]:
    counts = {field: Counter() for field in SOURCE_FIELDS}
    for field in SOURCE_FIELDS:
        blocks = question.get(field, [])
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key, kind in (("ole_occurrences", "ole"), ("omml_occurrences", "omml")):
                values = block.get(key, [])
                if isinstance(values, list):
                    counts[field][kind] += len(values)
    return counts


def _sourceable(question: dict[str, Any]) -> bool:
    for field in SOURCE_FIELDS:
        blocks = question.get(field, [])
        if isinstance(blocks, list) and any(
            isinstance(block, dict) and isinstance(block.get("document_id"), str)
            for block in blocks
        ):
            return True
    return False


def _preview_rows(block: dict[str, Any], occurrence: dict[str, Any], asset_hashes: dict[str, str]):
    rel_ids = occurrence.get("preview_rel_ids")
    media_paths = occurrence.get("preview_media_paths")
    rel_ids = rel_ids if isinstance(rel_ids, list) else []
    media_paths = media_paths if isinstance(media_paths, list) else []
    media_occurrences = block.get("media_occurrences", [])
    media_occurrences = media_occurrences if isinstance(media_occurrences, list) else []
    rows = []
    for index, media_path in enumerate(media_paths):
        rel_id = rel_ids[index] if index < len(rel_ids) else None
        matches = [
            item
            for item in media_occurrences
            if isinstance(item, dict)
            and item.get("kind") == "ole_preview"
            and item.get("ole_id") == occurrence.get("ole_id")
            and item.get("source_rel_id") == rel_id
            and item.get("media_path") == media_path
        ]
        rows.append(
            {
                "source_rel_id": rel_id,
                "media_path": media_path,
                "media_sha256": asset_hashes.get(media_path),
                "binding_status": "bound" if len(matches) == 1 else "missing_or_ambiguous",
            }
        )
    return rows


def _make_helper_question(field: str, block: dict[str, Any], occurrence_kind: str, occurrence: Any):
    """Adapt every source field through the frozen converter's common helper."""
    helper_field = field if field in CONVERTER_FIELDS else "stem_blocks"
    helper_block = {
        "document_id": block.get("document_id"),
        "ole_occurrences": [occurrence] if occurrence_kind == "ole_occurrences" else [],
        "omml_occurrences": [occurrence] if occurrence_kind == "omml_occurrences" else [],
    }
    return SimpleNamespace(source_payload={helper_field: [helper_block]})


def _conversion_record(conversion: Any) -> dict[str, Any]:
    data = conversion.model_dump(mode="json")
    return {
        "source_sha256": data.get("source_sha256"),
        "source_kind": data.get("source_kind"),
        "native_payload_sha256": data.get("native_payload_sha256"),
        "status": data.get("status"),
        "converter": data.get("converter"),
        "errors": data.get("errors", []),
    }


def diagnose(bank_dir: Path, semantic_dir: Path, converter_source: Path | None) -> dict[str, Any]:
    bank_dir = bank_dir.resolve(strict=True)
    semantic_dir = semantic_dir.resolve(strict=True)
    module, loaded_converter = _load_converter(converter_source)
    conversion_cache: dict[tuple[str, str], Any] = {}

    raw_bank, bank_bytes = _json_read(bank_dir / "bank.json")
    if not isinstance(raw_bank, dict) or not isinstance(raw_bank.get("questions"), list):
        raise ValueError("source bank must contain a questions array")
    raw_questions = raw_bank["questions"]
    question_ids = [row.get("id") for row in raw_questions if isinstance(row, dict)]
    if len(question_ids) != len(raw_questions) or any(
        not isinstance(qid, str) for qid in question_ids
    ):
        raise ValueError("source bank question identities are invalid")
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("source bank question IDs must be unique")
    raw_question_map = {row["id"]: row for row in raw_questions}
    raw_document_rows = raw_bank.get("documents", [])
    raw_documents = {
        item["id"]: item for item in raw_document_rows if isinstance(item, dict) and item.get("id")
    }

    semantic_annotations_sha = None
    semantic_curriculum_sha = None
    semantic_load_error = None
    try:
        annotations, annotation_bytes = _json_read(semantic_dir / "annotations.json")
        curriculum_raw, curriculum_bytes = _json_read(semantic_dir / "curriculum.json")
        del annotations, curriculum_raw
        semantic_annotations_sha = _sha256(annotation_bytes)
        semantic_curriculum_sha = _sha256(curriculum_bytes)
    except (OSError, json.JSONDecodeError):
        semantic_load_error = "semantic_input_unavailable"

    practice_bank = None
    try:
        practice_bank = load_practice_bank(bank_dir, semantic_dir)
    except Exception:
        semantic_load_error = "practice_bank_validation_failed"
    practice_questions = (
        {question.id: question for question in practice_bank.questions}
        if practice_bank is not None
        else {}
    )

    question_errors: dict[str, Counter[str]] = {qid: Counter() for qid in question_ids}
    question_occurrences: dict[str, Counter[str]] = {qid: Counter() for qid in question_ids}
    question_formula_hashes: dict[str, set[str]] = {qid: set() for qid in question_ids}
    field_questions: dict[str, set[str]] = {field: set() for field in SOURCE_FIELDS}
    field_occurrences: Counter[str] = Counter()
    raw_kind_occurrences: dict[str, Counter[str]] = {field: Counter() for field in SOURCE_FIELDS}
    field_hashes: dict[str, set[str]] = {field: set() for field in SOURCE_FIELDS}
    occurrences: list[dict[str, Any]] = []
    unique_formulas: dict[str, dict[str, Any]] = {}
    kind_unique_hashes: dict[str, set[str]] = {"ole": set(), "omml": set()}
    sourceable_questions = 0
    valid_source_bindings = 0
    eligible_question_ids = (
        {question.id for question in practice_bank.questions if question.status == "eligible"}
        if practice_bank is not None
        else set()
    )

    for qid in question_ids:
        raw_question = raw_question_map[qid]
        if _sourceable(raw_question):
            sourceable_questions += 1
        formula_counts = _formula_fields(raw_question)
        for field, kind_counts in _formula_kind_counts(raw_question).items():
            raw_kind_occurrences[field].update(kind_counts)
        for field, count in formula_counts.items():
            field_occurrences[field] += count
            if count:
                field_questions[field].add(qid)
                question_occurrences[qid][field] += count
        if not any(formula_counts.values()):
            continue

        question = practice_questions.get(qid)
        docs = None
        binding_error = None
        if question is None:
            binding_error = semantic_load_error or "practice_question_unavailable"
        else:
            try:
                docs = module._validate_source_binding(
                    question, raw_bank, bank_dir, raw_question_map, raw_documents
                )
                valid_source_bindings += 1
            except Exception as exc:
                binding_error = str(getattr(exc, "code", "source_binding_failed"))
        if binding_error:
            question_errors[qid][binding_error] += sum(formula_counts.values())

        for field in SOURCE_FIELDS:
            blocks = raw_question.get(field, [])
            if not isinstance(blocks, list):
                if blocks is not None:
                    question_errors[qid]["source_blocks_invalid"] += 1
                continue
            for block_index, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                for occurrence_kind, source_kind in (
                    ("ole_occurrences", "ole"),
                    ("omml_occurrences", "omml"),
                ):
                    values = block.get(occurrence_kind, [])
                    if not isinstance(values, list):
                        question_errors[qid]["formula_occurrences_invalid"] += 1
                        continue
                    for occurrence_index, source_occurrence in enumerate(values):
                        evidence: dict[str, Any] = {
                            "question_id": qid,
                            "source_field": field,
                            "document_id": block.get("document_id"),
                            "paragraph_index": block.get("paragraph_index"),
                            "block_index": block_index,
                            "occurrence_index": occurrence_index,
                            "source_kind": source_kind,
                            "source_part": None,
                            "ole_id": None,
                            "ole_rel_id": None,
                            "formula_sha256": None,
                            "native_payload_sha256": None,
                            "preview_evidence": [],
                            "binding_status": "unverified",
                            "conversion_status": "not_attempted",
                            "error_codes": [],
                        }
                        if not isinstance(source_occurrence, dict):
                            evidence["error_codes"] = ["formula_occurrence_invalid"]
                            question_errors[qid]["formula_occurrence_invalid"] += 1
                            occurrences.append(evidence)
                            continue
                        if binding_error:
                            evidence["binding_status"] = "source_binding_failed"
                            evidence["error_codes"] = [binding_error]
                            if source_kind == "ole":
                                evidence["source_part"] = source_occurrence.get("source_part")
                                evidence["ole_id"] = source_occurrence.get("ole_id")
                                evidence["ole_rel_id"] = source_occurrence.get("ole_rel_id")
                                evidence["preview_evidence"] = _preview_rows(
                                    block, source_occurrence, raw_question.get("asset_hashes", {})
                                )
                            else:
                                evidence["source_part"] = "embedded_mathml"
                            occurrences.append(evidence)
                            continue
                        helper_question = _make_helper_question(
                            field, block, occurrence_kind, source_occurrence
                        )
                        try:
                            converted_rows = _convert_occurrence_once(
                                module, helper_question, docs, conversion_cache
                            )
                            if len(converted_rows) != 1:
                                raise ValueError("formula_helper_cardinality_mismatch")
                            formula_sha, converted = next(iter(converted_rows.items()))
                            evidence["formula_sha256"] = formula_sha
                            evidence["conversion_status"] = converted.status
                            evidence["binding_status"] = "source_bound"
                            evidence["error_codes"] = list(converted.errors)
                            if source_kind == "ole":
                                evidence["source_part"] = source_occurrence.get("source_part")
                                evidence["ole_id"] = source_occurrence.get("ole_id")
                                evidence["ole_rel_id"] = source_occurrence.get("ole_rel_id")
                                native = source_occurrence.get("native_equation") or {}
                                evidence["native_payload_sha256"] = native.get("payload_sha256")
                                evidence["preview_evidence"] = _preview_rows(
                                    block, source_occurrence, question.asset_hashes
                                )
                                if not evidence["preview_evidence"] or any(
                                    item.get("binding_status") != "bound"
                                    or not item.get("media_sha256")
                                    for item in evidence["preview_evidence"]
                                ):
                                    evidence["error_codes"].append("ole_preview_binding_missing")
                                    question_errors[qid]["ole_preview_binding_missing"] += 1
                            else:
                                evidence["source_part"] = "embedded_mathml"
                                evidence["source_xml_sha256"] = formula_sha
                            field_hashes[field].add(formula_sha)
                            question_formula_hashes[qid].add(formula_sha)
                            kind_unique_hashes[source_kind].add(formula_sha)
                            formula_record = unique_formulas.get(formula_sha)
                            converted_record = _conversion_record(converted)
                            if formula_record is None:
                                unique_formulas[formula_sha] = {
                                    **converted_record,
                                    "first_seen_question_id": qid,
                                    "occurrence_count": 0,
                                }
                                formula_record = unique_formulas[formula_sha]
                            elif (
                                formula_record["source_kind"] != converted_record["source_kind"]
                                or formula_record["status"] != converted_record["status"]
                                or formula_record["errors"] != converted_record["errors"]
                            ):
                                question_errors[qid]["formula_hash_collision"] += 1
                                evidence["error_codes"].append("formula_hash_collision")
                            formula_record["occurrence_count"] += 1
                            if converted.status != "converted":
                                for code in converted.errors or ["formula_unsupported"]:
                                    question_errors[qid][code] += 1
                        except Exception as exc:
                            code = getattr(exc, "code", None)
                            if not isinstance(code, str):
                                code = (
                                    str(exc)
                                    if str(exc)
                                    in {
                                        "formula_helper_cardinality_mismatch",
                                        "formula_hash_collision",
                                    }
                                    else "formula_occurrence_failed"
                                )
                            evidence["binding_status"] = (
                                "source_binding_failed"
                                if "binding" in code or "source" in code or "ole_occurrence" in code
                                else "source_bound"
                            )
                            evidence["error_codes"] = [code]
                            question_errors[qid][code] += 1
                            if source_kind == "ole":
                                evidence["source_part"] = source_occurrence.get("source_part")
                                evidence["ole_id"] = source_occurrence.get("ole_id")
                                evidence["ole_rel_id"] = source_occurrence.get("ole_rel_id")
                                evidence["preview_evidence"] = _preview_rows(
                                    block, source_occurrence, question.asset_hashes
                                )
                            else:
                                evidence["source_part"] = "embedded_mathml"
                        occurrences.append(evidence)

    for evidence in occurrences:
        formula_sha = evidence.get("formula_sha256")
        if formula_sha and formula_sha in unique_formulas:
            evidence["conversion_status"] = unique_formulas[formula_sha]["status"]

    field_rows = {
        field: {
            "question_count": len(field_questions[field]),
            "occurrence_count": field_occurrences[field],
            "unique_formula_count": len(field_hashes[field]),
        }
        for field in SOURCE_FIELDS
    }
    unique_converted = sum(1 for row in unique_formulas.values() if row["status"] == "converted")
    unique_unsupported = sum(1 for row in unique_formulas.values() if row["status"] != "converted")
    export_fields = {"stem_blocks", "answer_blocks", "analysis_blocks"}
    export_rows = [row for row in occurrences if row["source_field"] in export_fields]
    export_formula_hashes = {
        row["formula_sha256"] for row in export_rows if row.get("formula_sha256")
    }
    export_question_ids = {row["question_id"] for row in export_rows}
    export_bound = [row for row in export_rows if row["binding_status"] == "source_bound"]
    export_converted_rows = [row for row in export_bound if row["conversion_status"] == "converted"]
    export_unsupported_rows = [
        row for row in export_bound if row["conversion_status"] != "converted"
    ]
    export_unsupported_questions = {row["question_id"] for row in export_unsupported_rows}
    export_formula_rows = [
        unique_formulas[sha] for sha in export_formula_hashes if sha in unique_formulas
    ]
    export_unique_converted = sum(row["status"] == "converted" for row in export_formula_rows)
    export_unique_unsupported = len(export_formula_rows) - export_unique_converted
    all_question_errors = Counter(
        code for errors in question_errors.values() for code in errors.elements()
    )
    occurrence_errors_by_field: dict[str, Counter[str]] = {
        field: Counter() for field in SOURCE_FIELDS
    }
    for row in occurrences:
        occurrence_errors_by_field[row["source_field"]].update(row.get("error_codes", []))
    converter_path = loaded_converter or Path(module.__file__).resolve(strict=True)
    summary = {
        "schema_version": "latex-bank-diagnostic-v1",
        "inputs": {
            "bank_sha256": _sha256(bank_bytes),
            "semantic_annotations_sha256": semantic_annotations_sha,
            "semantic_curriculum_sha256": semantic_curriculum_sha,
        },
        "converter": {
            "name": str(getattr(module, "_CONVERTER", "unknown")),
            "source_filename": converter_path.name,
            "source_sha256": _sha256(converter_path.read_bytes()),
        },
        "question_denominators": {
            "raw_questions": len(raw_questions),
            "eligible_questions": len(eligible_question_ids),
            "blocked_or_unavailable_questions": len(raw_questions) - len(eligible_question_ids),
            "sourceable_questions": sourceable_questions,
            "source_binding_valid_questions_with_formulas": valid_source_bindings,
            "questions_with_formula_occurrences": sum(
                1 for values in question_occurrences.values() if sum(values.values())
            ),
            "questions_with_formula_errors": sum(
                1 for values in question_errors.values() if values
            ),
        },
        "formula_denominators": {
            "diagnostic_ole_occurrences": sum(
                raw_kind_occurrences[field]["ole"] for field in SOURCE_FIELDS
            ),
            "diagnostic_omml_occurrences": sum(
                raw_kind_occurrences[field]["omml"] for field in SOURCE_FIELDS
            ),
            "diagnostic_total_occurrences": sum(field_occurrences.values()),
            "export_ole_occurrences": sum(
                raw_kind_occurrences[field]["ole"]
                for field in SOURCE_FIELDS
                if field != "analysis_stem_blocks"
            ),
            "export_omml_occurrences": sum(
                raw_kind_occurrences[field]["omml"]
                for field in SOURCE_FIELDS
                if field != "analysis_stem_blocks"
            ),
            "export_unique_formula_sha256": len(export_formula_hashes),
            "export_unique_converted": export_unique_converted,
            "export_unique_unsupported": export_unique_unsupported,
            "export_source_bound_occurrences": len(export_bound),
            "export_converted_occurrences": len(export_converted_rows),
            "export_unsupported_occurrences": len(export_unsupported_rows),
            "export_unverified_occurrences": len(export_rows) - len(export_bound),
            "export_questions_with_formula_occurrences": len(export_question_ids),
            "export_eligible_questions_with_formula_occurrences": len(
                export_question_ids & eligible_question_ids
            ),
            "export_questions_with_unsupported_formula": len(export_unsupported_questions),
            "export_eligible_questions_with_unsupported_formula": len(
                export_unsupported_questions & eligible_question_ids
            ),
            "source_bound_occurrences": sum(
                1 for row in occurrences if row["binding_status"] == "source_bound"
            ),
            "source_binding_failed_occurrences": sum(
                1 for row in occurrences if row["binding_status"] == "source_binding_failed"
            ),
            "unique_raw_ole_sha256": len(kind_unique_hashes["ole"]),
            "unique_raw_omml_sha256": len(kind_unique_hashes["omml"]),
            "unique_raw_formula_sha256": len(unique_formulas),
            "unique_converted": unique_converted,
            "unique_unsupported_or_unverified": unique_unsupported,
            "unique_unverified_occurrences": sum(
                1 for row in occurrences if row["binding_status"] != "source_bound"
            ),
            "occurrences_of_converted_unique_formulas": sum(
                1
                for row in occurrences
                if row.get("formula_sha256") in unique_formulas
                and unique_formulas[row["formula_sha256"]]["status"] == "converted"
            ),
            "occurrences_of_unsupported_unique_formulas": sum(
                1
                for row in occurrences
                if row.get("formula_sha256") in unique_formulas
                and unique_formulas[row["formula_sha256"]]["status"] != "converted"
            ),
        },
        "source_fields": field_rows,
        "occurrence_error_counts": dict(
            sorted(
                Counter(code for row in occurrences for code in row.get("error_codes", [])).items()
            )
        ),
        "occurrence_error_counts_by_source_field": {
            field: dict(sorted(counts.items()))
            for field, counts in occurrence_errors_by_field.items()
        },
        "question_error_counts": dict(sorted(all_question_errors.items())),
        "semantic_load_error": semantic_load_error,
        "limitations": [
            "Conversion coverage is an engineering diagnostic, not a semantic correctness or teacher-value result.",
            "Unique formula counts and occurrence counts are separate denominators.",
            "analysis_stem_blocks is a diagnostic source field and is excluded from export occurrence denominators.",
            "Blocked questions remain in the raw denominator; source-binding failures are recorded as unattempted occurrences.",
        ],
    }
    question_rows = []
    for qid in question_ids:
        question = practice_questions.get(qid)
        question_rows.append(
            {
                "question_id": qid,
                "eligibility": question.status if question else "unavailable",
                "sourceable": _sourceable(raw_question_map[qid]),
                "formula_occurrences_by_source_field": dict(question_occurrences[qid]),
                "unique_formula_sha256_count": len(question_formula_hashes[qid]),
                "error_counts": dict(sorted(question_errors[qid].items())),
            }
        )
    return {
        "summary": summary,
        "formulas": sorted(
            unique_formulas.values(), key=lambda row: (row["source_kind"], row["source_sha256"])
        ),
        "occurrences": occurrences,
        "questions": question_rows,
    }


def _write_report(report: dict[str, Any], destination: Path) -> None:
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
    try:
        for name, key in (
            ("summary.json", "summary"),
            ("formulas.json", "formulas"),
            ("occurrences.json", "occurrences"),
            ("questions.json", "questions"),
        ):
            (stage / name).write_text(
                json.dumps(report[key], ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        os.rename(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, required=True, help="source-bank-v3 directory")
    parser.add_argument("--semantic", type=Path, required=True, help="validated semantic directory")
    parser.add_argument(
        "--output", type=Path, required=True, help="new diagnostic output directory"
    )
    parser.add_argument(
        "--converter-source",
        type=Path,
        help="optional frozen converter Python source; defaults to backend.app.math_latex",
    )
    args = parser.parse_args(argv)
    try:
        bank = args.bank.resolve(strict=True)
        semantic = args.semantic.resolve(strict=True)
        destination = _check_output_path(args.output, bank, semantic)
        report = diagnose(bank, semantic, args.converter_source)
        _write_report(report, destination)
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        print(f"diagnosis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(
        json.dumps(
            {
                "output": str(destination),
                **summary["question_denominators"],
                **summary["formula_denominators"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
