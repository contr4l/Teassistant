from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from app.bank_practice import (
    build_bank_practice,
    load_practice_bank,
    validate_bank_curriculum,
    validate_bank_progress,
    validate_bank_snapshot,
)
from app.contracts import Curriculum, KnowledgePoint, PracticeRequest, Quota, StudentProgress
from app.semantic_bank import canonical_hash
from app.semantic_contracts import DIFFICULTY_VERSION


def _write_json(path: Path, value) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    path.write_bytes(raw)
    return raw


def _docx(path: Path, image: bytes | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"<document/>")
        if image is not None:
            zf.writestr("word/media/image1.png", image)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, *, warning: str | None = None):
    bank_dir = tmp_path / "bank"
    semantic_dir = tmp_path / "semantic"
    bank_dir.mkdir()
    semantic_dir.mkdir()
    (bank_dir / "media/doc-original").mkdir(parents=True)
    png = b"synthetic-preview-bytes"
    (bank_dir / "media/doc-original/image1.png").write_bytes(png)
    original_sha = _docx(bank_dir / "sources/original.docx", png)
    analysis_sha = _docx(bank_dir / "sources/analysis.docx")

    curriculum = Curriculum(
        version="catalog-v1",
        is_synthetic=True,
        points=[
            KnowledgePoint(id="p0", name="P0", prerequisite_ids=[]),
            KnowledgePoint(id="p1", name="P1", prerequisite_ids=["p0"]),
        ],
    )
    curriculum_bytes = _write_json(
        semantic_dir / "curriculum.json", curriculum.model_dump(mode="json")
    )
    source_docs = [
        {
            "id": "doc-original",
            "filename": "original.docx",
            "sha256": original_sha,
            "role": "original",
            "pair_id": "pair-1",
            "status": "imported",
            "source_copies": [{"filename": "original.docx", "path": "sources/original.docx"}],
        },
        {
            "id": "doc-analysis",
            "filename": "analysis.docx",
            "sha256": analysis_sha,
            "role": "analysis",
            "pair_id": "pair-1",
            "status": "imported",
            "source_copies": [{"filename": "analysis.docx", "path": "sources/analysis.docx"}],
        },
    ]
    media_occurrence = {
        "source_part": "word/media/image1.png",
        "media_path": "media/doc-original/image1.png",
        "rendered": True,
        "kind": "drawingml",
    }
    original_block = {
        "document_id": "doc-original",
        "paragraph_index": 1,
        "text": "synthetic question",
        "html": '<p>synthetic question<img src="media/doc-original/image1.png"/></p>',
        "media_paths": ["media/doc-original/image1.png"],
        "media_occurrences": [media_occurrence],
        "ole_occurrences": [],
        "omml_occurrences": [],
    }
    analysis_block = {
        "document_id": "doc-analysis",
        "paragraph_index": 2,
        "text": "synthetic explanation",
        "html": "<p>synthetic explanation</p>",
        "media_paths": [],
        "media_occurrences": [],
        "ole_occurrences": [],
        "omml_occurrences": [],
    }
    source_question = {
        "id": "pair-1-q1",
        "pair_id": "pair-1",
        "number": 1,
        "question_origin": "paired",
        "question_type": "choice",
        "section": "synthetic section",
        "stem_blocks": [original_block],
        "analysis_stem_blocks": [analysis_block],
        "answer_blocks": [analysis_block],
        "analysis_blocks": [analysis_block],
        "structure_status": "imported",
        "association_status": "confirmed",
        "association_evidence": {"method": "synthetic"},
        "association_comparison_method": "synthetic-test",
        "answer_available": True,
        "issues": [],
        "practice_ready": False,
        "content_fingerprint": "a" * 64,
        "fingerprint_version": "source-structure-v2",
    }
    bank_json = {
        "schema_version": "source-bank-v3",
        "batch_status": "succeeded",
        "documents": source_docs,
        "pairs": [],
        "questions": [source_question],
    }
    bank_bytes = _write_json(bank_dir / "bank.json", bank_json)
    source_run_dir = tmp_path / "source-run"
    source_run_dir.mkdir()
    assessment = {
        "question_id": source_question["id"],
        "status": "assessed",
        "point_ids": ["p1"],
        "prerequisite_ids": ["p0"],
        "difficulty_score": 55,
        "reasoning": "synthetic test only",
        "point_evidence": [
            {"point_id": "p1", "role": "target", "reason": "synthetic"},
            {"point_id": "p0", "role": "prerequisite", "reason": "synthetic"},
        ],
        "warnings": [warning] if warning else [],
    }
    call_dir = source_run_dir / "calls/0001"
    call_dir.mkdir(parents=True)
    prompt_raw = b"synthetic prompt"
    (call_dir / "prompt.txt").write_bytes(prompt_raw)
    request_raw = _write_json(
        call_dir / "request.json",
        {
            "question_ids": [source_question["id"]],
            "prompt_sha256": hashlib.sha256(prompt_raw).hexdigest(),
            "started_at": "synthetic",
        },
    )
    result_raw = _write_json(
        call_dir / "result.json",
        {
            "provider": "codex_cli",
            "model": "synthetic-model",
            "model_metadata_source": "configured_model",
            "question_ids": [source_question["id"]],
            "raw_json_result": {"assessments": [assessment]},
        },
    )
    _write_json(
        source_run_dir / "run.json",
        {
            "source_bank_sha256": hashlib.sha256(bank_bytes).hexdigest(),
            "difficulty_version": DIFFICULTY_VERSION,
            "question_ids": [source_question["id"]],
        },
    )
    annotation = {
        "status": "assessed",
        "association_status": "confirmed",
        "source_hash": canonical_hash(source_question),
        "assessment": assessment,
        "required_point_ids": ["p0", "p1"],
        "validated_catalog_version": curriculum.version,
        "validated_catalog_sha256": hashlib.sha256(curriculum_bytes).hexdigest(),
        "provenance": {
            "source_run_path": str(source_run_dir),
            "call_id": 1,
            "call_directory": "calls/0001",
            "request_sha256": hashlib.sha256(request_raw).hexdigest(),
            "prompt_sha256": hashlib.sha256(prompt_raw).hexdigest(),
            "result_sha256": hashlib.sha256(result_raw).hexdigest(),
            "response_item_sha256": canonical_hash(assessment),
        },
    }
    _write_json(
        semantic_dir / "annotations.json",
        {
            "run": {
                "schema_version": "source-semantic-migration-v1",
                "source_bank_sha256": hashlib.sha256(bank_bytes).hexdigest(),
                "validated_curriculum_sha256": hashlib.sha256(curriculum_bytes).hexdigest(),
                "curriculum_version": curriculum.version,
                "practice_ready": False,
            },
            "questions": {source_question["id"]: annotation},
        },
    )
    return bank_dir, semantic_dir, curriculum, source_question


def _progress(curriculum: Curriculum, *, revision="rev-1", learned=None):
    return StudentProgress(
        student_id="synthetic-student",
        revision=revision,
        curriculum_version=curriculum.version,
        learned_point_ids=learned if learned is not None else ["p0", "p1"],
    )


def _request():
    return PracticeRequest(
        quotas=[Quota(point_id="p1", count=1)],
        difficulty_version=DIFFICULTY_VERSION,
        seed=0,
    )


def test_loader_preserves_source_and_semantic_payload_and_verifies_media(tmp_path):
    bank_dir, semantic_dir, curriculum, original = _fixture(tmp_path)
    bank = load_practice_bank(bank_dir, semantic_dir)
    assert len(bank.questions) == 1
    question = bank.questions[0]
    assert question.status == "eligible"
    assert question.source_payload == original
    assert question.semantic_payload["assessment"]["question_id"] == original["id"]
    assert question.documents[0].path.startswith("sources/")
    assert question.asset_hashes == {
        "media/doc-original/image1.png": hashlib.sha256(b"synthetic-preview-bytes").hexdigest()
    }
    assert bank.curriculum_snapshot.model_dump() == curriculum.model_dump()


def test_loader_blocks_media_tampering_and_semantic_warnings(tmp_path):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path, warning="low confidence")
    (bank_dir / "media/doc-original/image1.png").write_bytes(b"tampered")
    bank = load_practice_bank(bank_dir, semantic_dir)
    question = bank.questions[0]
    assert question.status == "blocked"
    assert "semantic_warnings" in question.block_reasons
    assert "source_media_missing_or_mismatched" in question.block_reasons


def test_loader_rejects_changed_source_bank_provenance(tmp_path):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    path = bank_dir / "bank.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="different source bank"):
        load_practice_bank(bank_dir, semantic_dir)


def test_loader_checks_source_run_difficulty_version(tmp_path):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    source_run = tmp_path / "source-run" / "run.json"
    raw = json.loads(source_run.read_text())
    raw["difficulty_version"] = "unknown-version"
    _write_json(source_run, raw)
    bank = load_practice_bank(bank_dir, semantic_dir)
    assert bank.questions[0].status == "blocked"
    assert "semantic_source_run_mismatch" in bank.questions[0].block_reasons


def test_loader_rejects_annotation_changed_after_raw_call(tmp_path):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    annotations_path = semantic_dir / "annotations.json"
    annotations = json.loads(annotations_path.read_text())
    row = annotations["questions"]["pair-1-q1"]
    row["assessment"]["difficulty_score"] = 99
    _write_json(annotations_path, annotations)
    bank = load_practice_bank(bank_dir, semantic_dir)
    question = bank.questions[0]
    assert question.status == "blocked"
    assert "semantic_response_item_mismatch" in question.block_reasons


def test_selection_uses_allocator_and_export_validates_current_progress(tmp_path):
    bank_dir, semantic_dir, curriculum, _ = _fixture(tmp_path)
    bank = load_practice_bank(bank_dir, semantic_dir)
    progress = _progress(curriculum)
    snapshot = build_bank_practice(
        bank,
        curriculum,
        progress,
        _request(),
        is_synthetic_student=True,
    )
    assert [item.question.id for item in snapshot.items] == ["pair-1-q1"]
    assert snapshot.bank_sha256 == canonical_hash(bank.model_dump(mode="json"))
    validate_bank_snapshot(snapshot, bank)
    validate_bank_curriculum(bank, curriculum)
    # A newer progress revision is accepted if it still covers the selected question.
    validate_bank_progress(snapshot, curriculum, _progress(curriculum, revision="rev-2"))
    with pytest.raises(ValueError, match="excludes selected question"):
        validate_bank_progress(snapshot, curriculum, _progress(curriculum, learned=["p1"]))


def test_same_version_curriculum_mutation_is_rejected(tmp_path):
    bank_dir, semantic_dir, curriculum, _ = _fixture(tmp_path)
    bank = load_practice_bank(bank_dir, semantic_dir)
    changed = Curriculum(
        version=curriculum.version,
        is_synthetic=True,
        points=[
            KnowledgePoint(id="p0", name="P0", prerequisite_ids=[]),
            KnowledgePoint(id="p1", name="changed", prerequisite_ids=[]),
        ],
    )
    with pytest.raises(ValueError, match="contents differ"):
        validate_bank_curriculum(bank, changed)
    with pytest.raises(ValueError, match="contents differ"):
        build_bank_practice(
            bank,
            changed,
            _progress(changed),
            _request(),
            is_synthetic_student=True,
        )


def test_selection_rejects_wrong_difficulty_contract(tmp_path):
    bank_dir, semantic_dir, curriculum, _ = _fixture(tmp_path)
    bank = load_practice_bank(bank_dir, semantic_dir)
    request = PracticeRequest(quotas=[Quota(point_id="p1", count=1)])
    with pytest.raises(ValueError, match="difficulty version mismatch"):
        build_bank_practice(
            bank,
            curriculum,
            _progress(curriculum),
            request,
            is_synthetic_student=True,
        )
