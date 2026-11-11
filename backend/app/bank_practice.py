"""Bridge immutable source-bank and semantic snapshots into practice selection.

This is a development-validation path. It preserves source payloads and never
changes the legacy ``Question.ready`` state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from app.allocation import allocate_candidates
from app.bank_practice_contracts import (
    BankPracticeItem,
    BankPracticeSnapshot,
    BankQuestion,
    PracticeBank,
    SourceDocument,
)
from app.contracts import Curriculum, PracticeRequest, StudentProgress
from app.curriculum import CurriculumIndex
from app.native_equation import extract_native_equation
from app.semantic_bank import canonical_hash
from app.semantic_contracts import DIFFICULTY_VERSION, SemanticAssessment

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_BLOCK_FIELDS = ("stem_blocks", "analysis_stem_blocks", "answer_blocks", "analysis_blocks")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {path.name}") from exc


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("unsafe relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("unsafe relative path")
    resolved_root = root.resolve(strict=True)
    result = (resolved_root / Path(*pure.parts)).resolve(strict=True)
    result.relative_to(resolved_root)
    if not result.is_file():
        raise ValueError("expected a file")
    return result


def _safe_zip_part(part: Any) -> str:
    if not isinstance(part, str) or "\\" in part:
        raise ValueError("unsafe source package part")
    pure = PurePosixPath(part)
    if pure.is_absolute() or any(segment in {"", ".", ".."} for segment in pure.parts):
        raise ValueError("unsafe source package part")
    return pure.as_posix()


def _question_blocks(question: dict[str, Any]):
    for field in _BLOCK_FIELDS:
        blocks = question.get(field)
        if blocks is None:
            continue
        if not isinstance(blocks, list):
            yield field, None
            continue
        for block in blocks:
            yield field, block


def _verify_source_material(
    question: dict[str, Any],
    docs_by_id: dict[str, dict[str, Any]],
    bank_dir: Path,
    output_hash_cache: dict[str, str],
    output_bytes_cache: dict[str, bytes],
    source_zip_cache: dict[str, tuple[zipfile.ZipFile, set[str], dict[str, bytes]]],
) -> tuple[list[SourceDocument], dict[str, str], list[str]]:
    reasons: list[str] = []
    referenced_doc_ids: set[str] = set()
    asset_hashes: dict[str, str] = {}

    expected_roles = {
        "stem_blocks": "original",
        "analysis_stem_blocks": "analysis",
        "answer_blocks": "analysis",
        "analysis_blocks": "analysis",
    }
    for field, block in _question_blocks(question):
        if not isinstance(block, dict):
            reasons.append("invalid_source_block")
            continue
        doc_id = block.get("document_id")
        if isinstance(doc_id, str) and doc_id:
            referenced_doc_ids.add(doc_id)
        else:
            reasons.append("source_document_reference_missing")
        doc = docs_by_id.get(doc_id) if isinstance(doc_id, str) else None
        if isinstance(doc, dict):
            if doc.get("pair_id") != question.get("pair_id"):
                reasons.append("source_pair_mismatch")
            if doc.get("role") != expected_roles[field]:
                reasons.append("source_document_role_mismatch")

    documents: list[SourceDocument] = []
    for doc_id in sorted(referenced_doc_ids):
        doc = docs_by_id.get(doc_id)
        if not isinstance(doc, dict) or doc.get("status") != "imported":
            reasons.append("source_document_unavailable")
            continue
        copies = doc.get("source_copies")
        if not isinstance(copies, list) or not copies:
            reasons.append("source_document_copy_missing")
            continue
        selected = None
        for copy_record in copies:
            if not isinstance(copy_record, dict):
                continue
            try:
                source_path = _safe_path(bank_dir, copy_record.get("path"))
            except (ValueError, TypeError, OSError):
                continue
            selected = (copy_record, source_path)
            break
        if selected is None:
            reasons.append("source_document_copy_missing")
            continue
        copy_record, source_path = selected
        digest = _sha256(source_path.read_bytes())
        if digest != doc.get("sha256") or digest != copy_record.get("sha256", doc.get("sha256")):
            reasons.append("source_document_hash_mismatch")
            continue
        rel_path = copy_record.get("path")
        documents.append(SourceDocument(id=doc_id, path=rel_path, sha256=digest))
        try:
            if str(source_path) not in source_zip_cache:
                zf = zipfile.ZipFile(source_path)
                source_zip_cache[str(source_path)] = (zf, set(zf.namelist()), {})
        except (OSError, zipfile.BadZipFile):
            reasons.append("source_document_invalid")

    source_paths: dict[str, Path] = {}
    for document in documents:
        try:
            source_paths[document.id] = _safe_path(bank_dir, document.path)
        except (ValueError, OSError):
            reasons.append("source_document_copy_missing")

    for field, block in _question_blocks(question):
        if not isinstance(block, dict):
            continue
        doc_id = block.get("document_id")
        if doc_id not in source_paths:
            continue
        source_path = source_paths[doc_id]
        cache_key = str(source_path)
        cached = source_zip_cache.get(cache_key)
        if cached is None:
            reasons.append("source_document_invalid")
            continue
        zf, member_names, part_cache = cached

        media_occurrences = block.get("media_occurrences", [])
        if not isinstance(media_occurrences, list):
            reasons.append("invalid_media_occurrences")
            media_occurrences = []
        for occurrence in media_occurrences:
            if not isinstance(occurrence, dict) or occurrence.get("rendered") is not True:
                reasons.append("source_media_unrendered")
                continue
            media_path = occurrence.get("media_path")
            try:
                output_path = _safe_path(bank_dir, media_path)
                source_part = _safe_zip_part(occurrence.get("source_part"))
                if not source_part.startswith("word/media/"):
                    raise ValueError("unexpected source media part")
                if source_part not in member_names:
                    raise ValueError("missing source media part")
                output_digest = output_hash_cache.get(str(output_path))
                output_bytes = output_bytes_cache.get(str(output_path))
                if output_bytes is None:
                    output_bytes = output_path.read_bytes()
                    output_bytes_cache[str(output_path)] = output_bytes
                if output_digest is None:
                    output_digest = _sha256(output_bytes)
                    output_hash_cache[str(output_path)] = output_digest
                if source_part not in part_cache:
                    part_cache[source_part] = zf.read(source_part)
                source_bytes = part_cache[source_part]
                if output_bytes != source_bytes:
                    raise ValueError("source media bytes differ")
                asset_hashes[media_path] = output_digest
            except (ValueError, TypeError, OSError, KeyError, zipfile.BadZipFile):
                reasons.append("source_media_missing_or_mismatched")

        ole_occurrences = block.get("ole_occurrences", [])
        if not isinstance(ole_occurrences, list):
            reasons.append("invalid_ole_occurrences")
            ole_occurrences = []
        for occurrence in ole_occurrences:
            if not isinstance(occurrence, dict):
                reasons.append("invalid_ole_occurrence")
                continue
            try:
                source_part = _safe_zip_part(occurrence.get("source_part"))
                if (
                    not source_part.startswith("word/embeddings/")
                    or source_part not in member_names
                ):
                    raise ValueError("missing OLE package part")
                if source_part not in part_cache:
                    part_cache[source_part] = zf.read(source_part)
                actual = extract_native_equation(part_cache[source_part])
                if actual.get("status") != "valid":
                    raise ValueError("native equation unavailable")
                if occurrence.get("native_equation") != actual:
                    raise ValueError("native equation evidence changed")
            except (ValueError, TypeError, OSError, KeyError, zipfile.BadZipFile):
                reasons.append("source_ole_missing_or_invalid")

        omml_occurrences = block.get("omml_occurrences", [])
        if not isinstance(omml_occurrences, list):
            reasons.append("invalid_omml_occurrences")
            omml_occurrences = []
        for occurrence in omml_occurrences:
            if not isinstance(occurrence, dict) or occurrence.get("supported") is not True:
                reasons.append("unsupported_omml")

    # Every question must be linked to both an original and an analysis document.
    roles = {docs_by_id.get(doc_id, {}).get("role") for doc_id in referenced_doc_ids}
    if not {"original", "analysis"}.issubset(roles):
        reasons.append("source_pair_incomplete")
    return documents, asset_hashes, sorted(set(reasons))


def _canonical_id(question: dict[str, Any], index: int) -> str:
    fingerprint = question.get("content_fingerprint")
    if isinstance(fingerprint, str) and _HEX_256.fullmatch(fingerprint):
        return f"content:{fingerprint}"
    # Invalid/missing fingerprints never collapse separate source rows.
    return f"unfingerprinted:{question.get('id', index)}:{index}"


def _annotation_reasons(
    question: dict[str, Any],
    row: Any,
    curriculum: Curriculum,
    catalog_sha256: str,
    curriculum_index: CurriculumIndex,
    semantic_root: Path,
    source_bank_sha256: str,
    call_cache: dict[
        tuple[str, int, str | None, str | None, str | None],
        tuple[dict[str, Any] | None, str | None],
    ],
) -> tuple[list[str], SemanticAssessment | None]:
    reasons: list[str] = []
    if not isinstance(row, dict):
        return ["semantic_annotation_missing"], None
    if row.get("status") != "assessed":
        reasons.append("semantic_not_assessed")
    if row.get("association_status") != question.get("association_status"):
        reasons.append("semantic_association_mismatch")
    if row.get("source_hash") != canonical_hash(question):
        reasons.append("semantic_source_hash_mismatch")
    if row.get("validated_catalog_version") != curriculum.version:
        reasons.append("semantic_catalog_version_mismatch")
    if row.get("validated_catalog_sha256") != catalog_sha256:
        reasons.append("semantic_catalog_hash_mismatch")

    provenance = row.get("provenance")
    try:
        source_run_path = Path(provenance["source_run_path"]).resolve(strict=True)
        source_run_path.relative_to(semantic_root.parent.resolve(strict=True))
        run_path = (source_run_path / "run.json").resolve(strict=True)
        run_path.relative_to(semantic_root.parent.resolve(strict=True))
        source_run, _ = _read_json(run_path)
        if (
            not isinstance(source_run, dict)
            or source_run.get("source_bank_sha256") != source_bank_sha256
            or source_run.get("difficulty_version") != DIFFICULTY_VERSION
            or question.get("id") not in source_run.get("question_ids", [])
        ):
            reasons.append("semantic_source_run_mismatch")
        call_id = provenance.get("call_id")
        call_dir = provenance.get("call_directory")
        if type(call_id) is not int or call_id < 1 or call_dir != f"calls/{call_id:04d}":
            raise ValueError("invalid source call reference")
        cache_key = (
            str(source_run_path),
            call_id,
            provenance.get("request_sha256"),
            provenance.get("prompt_sha256"),
            provenance.get("result_sha256"),
        )
        if cache_key not in call_cache:
            call_cache[cache_key] = _read_semantic_call(
                source_run_path,
                call_id,
                source_run,
                provenance,
            )
        call_items, call_error = call_cache[cache_key]
        if call_error:
            reasons.append(call_error)
        else:
            raw_item = (call_items or {}).get(question.get("id"))
            if raw_item is None:
                reasons.append("semantic_response_item_missing")
            elif (
                provenance.get("response_item_sha256") != canonical_hash(raw_item)
                or row.get("assessment") != raw_item
            ):
                reasons.append("semantic_response_item_mismatch")
    except (KeyError, TypeError, ValueError, OSError):
        reasons.append("semantic_source_run_unavailable")

    raw_assessment = row.get("assessment")
    assessment = None
    try:
        assessment = SemanticAssessment.model_validate(raw_assessment)
    except (ValidationError, TypeError, ValueError):
        reasons.append("semantic_assessment_invalid")
    if assessment is not None:
        if assessment.question_id != question.get("id"):
            reasons.append("semantic_question_id_mismatch")
        if assessment.status != "assessed":
            reasons.append("semantic_not_assessed")
        if assessment.warnings:
            reasons.append("semantic_warnings")
        try:
            required = sorted(
                curriculum_index.required([*assessment.point_ids, *assessment.prerequisite_ids])
            )
        except ValueError:
            reasons.append("semantic_unknown_curriculum_id")
        else:
            if row.get("required_point_ids") != required:
                reasons.append("semantic_required_closure_mismatch")
        if assessment.difficulty_score is None:
            reasons.append("semantic_difficulty_missing")
        elif (
            type(assessment.difficulty_score) is not int
            or not 0 <= assessment.difficulty_score <= 100
        ):
            reasons.append("semantic_difficulty_invalid")
    return sorted(set(reasons)), assessment


def _read_semantic_call(
    source_run_path: Path,
    call_id: int,
    source_run: dict[str, Any],
    provenance: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Verify immutable on-disk request/prompt/result and index raw response items."""
    relative = f"calls/{call_id:04d}"
    try:
        request_path = _safe_path(source_run_path, f"{relative}/request.json")
        prompt_path = _safe_path(source_run_path, f"{relative}/prompt.txt")
        result_path = _safe_path(source_run_path, f"{relative}/result.json")
        request_raw = request_path.read_bytes()
        prompt_raw = prompt_path.read_bytes()
        result_raw = result_path.read_bytes()
        if (
            _sha256(request_raw) != provenance.get("request_sha256")
            or _sha256(prompt_raw) != provenance.get("prompt_sha256")
            or _sha256(result_raw) != provenance.get("result_sha256")
        ):
            return None, "semantic_call_digest_mismatch"
        request = json.loads(request_raw)
        result = json.loads(result_raw)
        if not isinstance(request, dict) or not isinstance(result, dict):
            return None, "semantic_call_invalid"
        qids = request.get("question_ids")
        if (
            not isinstance(qids, list)
            or not qids
            or any(not isinstance(qid, str) for qid in qids)
            or len(qids) != len(set(qids))
            or not set(qids) <= set(source_run.get("question_ids", []))
            or request.get("prompt_sha256") != _sha256(prompt_raw)
            or result.get("question_ids") != qids
        ):
            return None, "semantic_call_cohort_mismatch"
        result_payload = result.get("raw_json_result")
        predictions = (
            result_payload.get("assessments") if isinstance(result_payload, dict) else None
        )
        if not isinstance(predictions, list):
            return None, "semantic_call_raw_result_invalid"
        indexed: dict[str, Any] = {}
        for prediction in predictions:
            if not isinstance(prediction, dict) or not isinstance(
                prediction.get("question_id"), str
            ):
                return None, "semantic_call_raw_result_invalid"
            prediction_id = prediction["question_id"]
            if prediction_id not in qids or prediction_id in indexed:
                return None, "semantic_call_raw_result_invalid"
            indexed[prediction_id] = prediction
        return indexed, None
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "semantic_call_unavailable"


def load_practice_bank(bank_dir: Path, semantic_dir: Path) -> PracticeBank:
    """Load and bind source-bank v3 to its validated semantic migration."""
    bank_root = bank_dir.resolve(strict=True)
    semantic_root = semantic_dir.resolve(strict=True)
    source_raw, source_bytes = _read_json(bank_root / "bank.json")
    curriculum_raw, curriculum_bytes = _read_json(semantic_root / "curriculum.json")
    annotations_raw, annotations_bytes = _read_json(semantic_root / "annotations.json")
    if not isinstance(source_raw, dict) or source_raw.get("schema_version") != "source-bank-v3":
        raise ValueError("unsupported source bank schema")
    if not isinstance(annotations_raw, dict):
        raise ValueError("invalid semantic annotations")
    run = annotations_raw.get("run")
    annotations = annotations_raw.get("questions")
    if not isinstance(run, dict) or not isinstance(annotations, dict):
        raise ValueError("semantic migration provenance is missing")

    source_sha = _sha256(source_bytes)
    curriculum_sha = _sha256(curriculum_bytes)
    annotations_sha = _sha256(annotations_bytes)
    if run.get("source_bank_sha256") != source_sha:
        raise ValueError("semantic migration refers to a different source bank")
    if run.get("validated_curriculum_sha256") != curriculum_sha:
        raise ValueError("semantic migration curriculum hash mismatch")
    curriculum = Curriculum.model_validate(curriculum_raw)
    if run.get("curriculum_version") != curriculum.version:
        raise ValueError("semantic migration curriculum version mismatch")
    if run.get("practice_ready") is not False:
        raise ValueError("semantic migration must not claim practice readiness")
    curriculum_index = CurriculumIndex(curriculum)

    raw_questions = source_raw.get("questions")
    raw_documents = source_raw.get("documents")
    if not isinstance(raw_questions, list) or not isinstance(raw_documents, list):
        raise ValueError("source bank questions or documents are invalid")
    ids = [q.get("id") for q in raw_questions if isinstance(q, dict)]
    if len(ids) != len(raw_questions) or any(not isinstance(qid, str) or not qid for qid in ids):
        raise ValueError("invalid source question identity")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source question identity")
    if set(annotations) != set(ids):
        raise ValueError("semantic annotation cohort differs from source bank")
    docs_by_id = {doc.get("id"): doc for doc in raw_documents if isinstance(doc, dict)}
    if len(docs_by_id) != len(raw_documents) or None in docs_by_id:
        raise ValueError("invalid source document index")

    output_hash_cache: dict[str, str] = {}
    output_bytes_cache: dict[str, bytes] = {}
    source_zip_cache: dict[str, tuple[zipfile.ZipFile, set[str], dict[str, bytes]]] = {}
    semantic_call_cache: dict[
        tuple[str, int, str | None, str | None, str | None],
        tuple[dict[str, Any] | None, str | None],
    ] = {}
    practice_questions: list[BankQuestion] = []
    for position, source_question in enumerate(raw_questions):
        qid = source_question["id"]
        annotation = annotations.get(qid)
        reasons: list[str] = []
        if source_question.get("structure_status") != "imported":
            reasons.append("source_structure_incomplete")
        if source_question.get("association_status") != "confirmed":
            reasons.append("source_association_unconfirmed")
        if source_question.get("answer_available") is not True:
            reasons.append("source_answer_missing")
        fingerprint = source_question.get("content_fingerprint")
        if not isinstance(fingerprint, str) or not _HEX_256.fullmatch(fingerprint):
            reasons.append("source_fingerprint_missing_or_invalid")

        documents, asset_hashes, source_reasons = _verify_source_material(
            source_question,
            docs_by_id,
            bank_root,
            output_hash_cache,
            output_bytes_cache,
            source_zip_cache,
        )
        reasons.extend(source_reasons)
        semantic_reasons, assessment = _annotation_reasons(
            source_question,
            annotation,
            curriculum,
            curriculum_sha,
            curriculum_index,
            semantic_root,
            source_sha,
            semantic_call_cache,
        )
        reasons.extend(semantic_reasons)
        reasons = sorted(set(reasons))

        point_ids = list(assessment.point_ids) if assessment else []
        prerequisite_ids = list(assessment.prerequisite_ids) if assessment else []
        score = assessment.difficulty_score if assessment else None
        source_hash = canonical_hash(source_question)
        semantic_hash = canonical_hash(annotation)
        revision = canonical_hash({"source_hash": source_hash, "semantic_hash": semantic_hash})
        row_fingerprint = (
            fingerprint
            if isinstance(fingerprint, str) and _HEX_256.fullmatch(fingerprint)
            else None
        )
        status = "eligible" if not reasons else "blocked"
        # Failed provenance and source rows remain visible; never silently discard them.
        try:
            practice_questions.append(
                BankQuestion(
                    id=qid,
                    revision=revision,
                    canonical_id=_canonical_id(source_question, position),
                    content_fingerprint=row_fingerprint,
                    curriculum_version=curriculum.version,
                    difficulty_version=DIFFICULTY_VERSION,
                    point_ids=point_ids,
                    prerequisite_ids=prerequisite_ids,
                    difficulty_score=score,
                    status=status,
                    block_reasons=reasons,
                    source_hash=source_hash,
                    semantic_hash=semantic_hash,
                    source_payload=copy.deepcopy(source_question),
                    semantic_payload=copy.deepcopy(annotation),
                    documents=documents,
                    asset_hashes=asset_hashes,
                )
            )
        except (ValidationError, ValueError):
            # Contract validation is an additional gate. Preserve row and diagnostic reason.
            practice_questions.append(
                BankQuestion(
                    id=qid,
                    revision=revision,
                    canonical_id=_canonical_id(source_question, position),
                    content_fingerprint=row_fingerprint,
                    curriculum_version=curriculum.version,
                    difficulty_version=DIFFICULTY_VERSION,
                    point_ids=point_ids,
                    prerequisite_ids=prerequisite_ids,
                    difficulty_score=score,
                    status="blocked",
                    block_reasons=sorted(set([*reasons, "practice_contract_validation_failed"])),
                    source_hash=source_hash,
                    semantic_hash=semantic_hash,
                    source_payload=copy.deepcopy(source_question),
                    semantic_payload=copy.deepcopy(annotation),
                    documents=documents,
                    asset_hashes=asset_hashes,
                )
            )

    for zf, _, _ in source_zip_cache.values():
        try:
            zf.close()
        except Exception:
            pass

    return PracticeBank(
        source_bank_sha256=source_sha,
        annotations_sha256=annotations_sha,
        curriculum_sha256=curriculum_sha,
        curriculum_version=curriculum.version,
        curriculum_snapshot=curriculum.model_copy(deep=True),
        questions=practice_questions,
    )


def validate_bank_progress(
    snapshot: BankPracticeSnapshot, curriculum: Curriculum, progress: StudentProgress
) -> None:
    """Recheck the current student's learning closure at the export boundary."""
    if snapshot.student_id != progress.student_id:
        raise ValueError("snapshot belongs to a different student")
    if (
        snapshot.curriculum_version != curriculum.version
        or progress.curriculum_version != curriculum.version
    ):
        raise ValueError("curriculum version mismatch")
    index = CurriculumIndex(curriculum)
    index.validate_ids(progress.learned_point_ids)
    if not progress.learned_point_ids:
        raise ValueError("student progress is not set")
    learned = set(progress.learned_point_ids)
    index.validate_ids([quota.point_id for quota in snapshot.request.quotas])
    for item in snapshot.items:
        question = item.question
        if question.status != "eligible":
            raise ValueError("snapshot includes a blocked question")
        if question.curriculum_version != curriculum.version:
            raise ValueError("question curriculum version mismatch")
        if question.difficulty_version != snapshot.request.difficulty_version:
            raise ValueError("question difficulty version mismatch")
        if not index.required(question.point_ids + question.prerequisite_ids) <= learned:
            raise ValueError("current student progress excludes selected question")


def validate_bank_curriculum(bank: PracticeBank, curriculum: Curriculum) -> None:
    """Reject a changed catalog even when its version string was reused."""
    bank = PracticeBank.model_validate(bank.model_dump())
    curriculum = Curriculum.model_validate(curriculum.model_dump())
    if curriculum.model_dump(mode="json") != bank.curriculum_snapshot.model_dump(mode="json"):
        raise ValueError("curriculum contents differ from source bank snapshot")


def build_bank_practice(
    bank: PracticeBank,
    curriculum: Curriculum,
    progress: StudentProgress,
    request: PracticeRequest,
    *,
    is_synthetic_student: bool,
) -> BankPracticeSnapshot:
    """Select from eligible source-native rows with the shared global allocator."""
    bank = PracticeBank.model_validate(bank.model_dump())
    curriculum = Curriculum.model_validate(curriculum.model_dump())
    progress = StudentProgress.model_validate(progress.model_dump())
    request = PracticeRequest.model_validate(request.model_dump())
    validate_bank_curriculum(bank, curriculum)
    if request.difficulty_version != DIFFICULTY_VERSION:
        raise ValueError("practice request difficulty version mismatch")
    index = CurriculumIndex(curriculum)
    if progress.curriculum_version != curriculum.version:
        raise ValueError("student progress curriculum version mismatch")
    index.validate_ids(progress.learned_point_ids)
    if not progress.learned_point_ids:
        raise ValueError("student progress is not set")
    index.validate_ids([quota.point_id for quota in request.quotas])
    learned = set(progress.learned_point_ids)

    eligible_indices: set[int] = set()
    for i, question in enumerate(bank.questions):
        if question.status != "eligible":
            continue
        if question.curriculum_version != curriculum.version:
            raise ValueError("question curriculum version mismatch")
        if question.difficulty_version != request.difficulty_version:
            raise ValueError("question difficulty version mismatch")
        required = index.required(question.point_ids + question.prerequisite_ids)
        if required <= learned:
            eligible_indices.add(i)

    allocation = allocate_candidates(bank.questions, request, eligible_indices)
    items = [
        BankPracticeItem(
            order=order,
            target_point_id=point_id,
            question=bank.questions[i].model_copy(deep=True),
        )
        for order, (i, point_id) in enumerate(allocation.selections, 1)
    ]
    return BankPracticeSnapshot(
        is_synthetic_student=is_synthetic_student,
        student_id=progress.student_id,
        progress_revision=progress.revision,
        curriculum_version=curriculum.version,
        bank_sha256=canonical_hash(bank.model_dump(mode="json")),
        request=request.model_copy(deep=True),
        items=items,
        shortfalls=allocation.shortfalls,
    )


def validate_bank_snapshot(snapshot: BankPracticeSnapshot, bank: PracticeBank) -> None:
    """Reject mutated or stale source snapshots before export."""
    snapshot = BankPracticeSnapshot.model_validate(snapshot.model_dump())
    bank = PracticeBank.model_validate(bank.model_dump())
    expected_bank_hash = canonical_hash(bank.model_dump(mode="json"))
    if snapshot.bank_sha256 != expected_bank_hash:
        raise ValueError("snapshot source bank hash mismatch")
    bank_by_id = {question.id: question for question in bank.questions}
    for item in snapshot.items:
        current = bank_by_id.get(item.question.id)
        if current is None or item.question.model_dump() != current.model_dump():
            raise ValueError("snapshot question differs from source bank")
        if canonical_hash(item.question.source_payload) != item.question.source_hash:
            raise ValueError("snapshot source payload changed")
        if canonical_hash(item.question.semantic_payload) != item.question.semantic_hash:
            raise ValueError("snapshot semantic payload changed")
        if item.question.status != "eligible":
            raise ValueError("snapshot includes a blocked question")
