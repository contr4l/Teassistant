from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

from app.bank_practice import load_practice_bank
from app.semantic_bank import canonical_hash
from app.semantic_contracts import DIFFICULTY_VERSION
from test_bank_practice import _fixture, _write_json


def _many_questions_16_sources(tmp_path: Path, count: int = 200):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bank_dir, semantic_dir, _curriculum, question = _fixture(tmp_path)
    bank_path = bank_dir / "bank.json"
    bank = json.loads(bank_path.read_bytes())
    source_run = tmp_path / "source-run"
    run_path = source_run / "run.json"
    annotations_path = semantic_dir / "annotations.json"
    annotations_doc = json.loads(annotations_path.read_bytes())
    original_annotation = next(iter(annotations_doc["questions"].values()))
    source_docs = bank["documents"]
    pair_docs: list[tuple[dict, dict]] = [(source_docs[0], source_docs[1])]

    for pair_index in range(1, 8):
        pair_id = f"pair-{pair_index + 1}"
        copied_docs = []
        for role, template in (("original", source_docs[0]), ("analysis", source_docs[1])):
            doc = copy.deepcopy(template)
            doc_id = f"doc-{role}-{pair_index + 1}"
            rel_path = f"sources/{role}-{pair_index + 1}.docx"
            shutil.copyfile(bank_dir / template["source_copies"][0]["path"], bank_dir / rel_path)
            digest = hashlib.sha256((bank_dir / rel_path).read_bytes()).hexdigest()
            doc.update(
                {
                    "id": doc_id,
                    "pair_id": pair_id,
                    "filename": f"{role}-{pair_index + 1}.docx",
                    "sha256": digest,
                    "source_copies": [{"filename": f"{role}-{pair_index + 1}.docx", "path": rel_path, "sha256": digest}],
                }
            )
            copied_docs.append(doc)
        bank["documents"].extend(copied_docs)
        pair_docs.append((copied_docs[0], copied_docs[1]))

    questions = []
    annotations = {}
    per_pair = count // len(pair_docs)
    for i in range(count):
        pair_index = min(i // per_pair, len(pair_docs) - 1)
        original_doc, analysis_doc = pair_docs[pair_index]
        row = copy.deepcopy(question)
        qid = f"synthetic-q-{i + 1}"
        row.update(
            {
                "id": qid,
                "pair_id": original_doc["pair_id"],
                "number": i + 1,
                "content_fingerprint": f"{i + 1:064x}",
            }
        )
        for field in ("stem_blocks", "analysis_stem_blocks", "answer_blocks", "analysis_blocks"):
            for block in row[field]:
                block["document_id"] = (
                    original_doc["id"] if field == "stem_blocks" else analysis_doc["id"]
                )
        questions.append(row)
        annotation = copy.deepcopy(original_annotation)
        assessment = annotation["assessment"]
        assessment["question_id"] = qid
        annotation["source_hash"] = canonical_hash(row)
        annotation["provenance"]["response_item_sha256"] = canonical_hash(assessment)
        annotations[qid] = annotation

    bank["questions"] = questions
    bank_bytes = _write_json(bank_path, bank)
    base_call = source_run / "calls/0001"
    prompt_raw = (base_call / "prompt.txt").read_bytes()
    qids = [row["id"] for row in questions]
    request_raw = _write_json(
        base_call / "request.json",
        {"question_ids": qids, "prompt_sha256": hashlib.sha256(prompt_raw).hexdigest(), "started_at": "synthetic"},
    )
    result = json.loads((base_call / "result.json").read_bytes())
    result["question_ids"] = qids
    result["raw_json_result"]["assessments"] = [annotations[qid]["assessment"] for qid in qids]
    result_raw = _write_json(base_call / "result.json", result)
    _write_json(
        run_path,
        {
            "source_bank_sha256": hashlib.sha256(bank_bytes).hexdigest(),
            "difficulty_version": DIFFICULTY_VERSION,
            "question_ids": qids,
        },
    )
    annotations_doc["questions"] = annotations
    annotations_doc["run"]["source_bank_sha256"] = hashlib.sha256(bank_bytes).hexdigest()
    annotations_path.write_bytes(json.dumps(annotations_doc, ensure_ascii=False, indent=2).encode())
    # Keep request/result variables used above explicit: their digests are provenance inputs.
    for annotation in annotations.values():
        annotation["provenance"]["request_sha256"] = hashlib.sha256(request_raw).hexdigest()
        annotation["provenance"]["result_sha256"] = hashlib.sha256(result_raw).hexdigest()
    annotations_path.write_bytes(json.dumps(annotations_doc, ensure_ascii=False, indent=2).encode())
    return bank_dir, semantic_dir, bank


def test_loader_reads_each_shared_docx_once_per_explicit_load(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _bank = _many_questions_16_sources(tmp_path)
    original_read_bytes = Path.read_bytes
    docx_reads: list[Path] = []

    def counted_read_bytes(path: Path) -> bytes:
        if path.suffix == ".docx":
            docx_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    first = load_practice_bank(bank_dir, semantic_dir)
    assert len(first.questions) == 200
    assert {question.status for question in first.questions} == {"eligible"}
    assert len(docx_reads) == 16
    assert len({path.resolve() for path in docx_reads}) == 16

    docx_reads.clear()
    second = load_practice_bank(bank_dir, semantic_dir)
    assert len(second.questions) == 200
    assert len(docx_reads) == 16


def test_cached_document_digest_still_checks_every_copy_record(tmp_path):
    bank_dir, semantic_dir, bank = _many_questions_16_sources(tmp_path)
    bank["documents"][0]["source_copies"][0]["sha256"] = "0" * 64
    _write_json(bank_dir / "bank.json", bank)
    # The changed source bank provenance intentionally makes this a hard loader error;
    # regenerate the semantic run binding so the per-document check is reached.
    bank_bytes = (bank_dir / "bank.json").read_bytes()
    annotations_path = semantic_dir / "annotations.json"
    annotations = json.loads(annotations_path.read_bytes())
    annotations["run"]["source_bank_sha256"] = hashlib.sha256(bank_bytes).hexdigest()
    _write_json(annotations_path, annotations)
    run_path = tmp_path / "source-run/run.json"
    run = json.loads(run_path.read_bytes())
    run["source_bank_sha256"] = hashlib.sha256(bank_bytes).hexdigest()
    _write_json(run_path, run)
    loaded = load_practice_bank(bank_dir, semantic_dir)
    first_pair_rows = [q for q in loaded.questions if q.source_payload["pair_id"] == "pair-1"]
    assert len(first_pair_rows) == 25
    assert all(q.status == "blocked" for q in first_pair_rows)
    assert all("source_document_hash_mismatch" in q.block_reasons for q in first_pair_rows)


def test_new_load_detects_docx_png_and_semantic_tampering(tmp_path):
    bank_dir, semantic_dir, _bank = _many_questions_16_sources(tmp_path)
    source_doc = bank_dir / "sources/original.docx"
    source_doc.write_bytes(source_doc.read_bytes() + b"tampered")
    loaded = load_practice_bank(bank_dir, semantic_dir)
    assert "source_document_hash_mismatch" in loaded.questions[0].block_reasons

    bank_dir, semantic_dir, _bank = _many_questions_16_sources(tmp_path / "png-case")
    (bank_dir / "media/doc-original/image1.png").write_bytes(b"tampered png")
    loaded = load_practice_bank(bank_dir, semantic_dir)
    assert "source_media_missing_or_mismatched" in loaded.questions[0].block_reasons

    bank_dir, semantic_dir, _bank = _many_questions_16_sources(tmp_path / "semantic-case")
    run_path = tmp_path / "semantic-case/source-run/run.json"
    run = json.loads(run_path.read_bytes())
    run["difficulty_version"] = "tampered"
    _write_json(run_path, run)
    loaded = load_practice_bank(bank_dir, semantic_dir)
    assert "semantic_source_run_mismatch" in loaded.questions[0].block_reasons
