"""Synthetic source-image integrity regression for the association evaluator."""

import importlib.util
import json
import zipfile
from pathlib import Path

from PIL import Image


def _load_evaluator():
    path = Path(__file__).resolve().parents[2] / "scripts/evaluate_associations.py"
    spec = importlib.util.spec_from_file_location("evaluate_associations_media", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png(path: Path, color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", (2, 2), color).save(path, format="PNG")


def test_output_media_tampering_cannot_keep_source_association_confirmed(tmp_path):
    evaluator = _load_evaluator()
    (tmp_path / "sources").mkdir()
    (tmp_path / "media").mkdir()
    source_png = tmp_path / "source.png"
    _png(source_png, (10, 20, 30, 255))
    image_bytes = source_png.read_bytes()

    documents = []
    sources = []
    for role in ("original", "analysis"):
        filename = f"synthetic-{role}.docx"
        path = tmp_path / "sources" / filename
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", b"<synthetic/>")
            archive.writestr("word/_rels/document.xml.rels", b"<synthetic/>")
            archive.writestr("word/media/image.png", image_bytes)
        sha = evaluator.digest(path.read_bytes())
        source = {
            "document_id": role,
            "filename": filename,
            "sha256": sha,
            "bytes": path.stat().st_size,
            "document_xml_sha256": evaluator.digest(b"<synthetic/>"),
            "relationships_sha256": evaluator.digest(b"<synthetic/>"),
            "pair_id": "synthetic-pair",
            "role": role,
        }
        sources.append(source)
        documents.append({"id": role, "sha256": sha, "pair_id": "synthetic-pair", "role": role})
        (tmp_path / "media" / f"{role}.png").write_bytes(image_bytes)

    def block(role: str) -> dict:
        return {
            "document_id": role,
            "paragraph_index": 0,
            "text": "合成题",
            "html": f'<p>合成题<img src="media/{role}.png"/></p>',
            "media_occurrences": [
                {
                    "kind": "drawingml",
                    "media_path": f"media/{role}.png",
                    "source_part": "word/media/image.png",
                    "rendered": True,
                    "paragraph_index": 0,
                    "occurrence": 0,
                }
            ],
        }

    stem = block("original")
    analysis_stem = block("analysis")
    answer = [
        {"document_id": "analysis", "paragraph_index": 1, "text": "答案", "html": "<p>答案</p>"}
    ]
    question = {
        "id": "synthetic-q1",
        "pair_id": "synthetic-pair",
        "number": 1,
        "source_question_range": [0, 0],
        "analysis_question_range": [0, 0],
        "stem_blocks": [stem],
        "analysis_stem_blocks": [analysis_stem],
        "answer_blocks": answer,
        "structure_status": "imported",
        "answer_available": True,
        "association_status": "confirmed",
    }
    pair = {
        "id": "synthetic-pair",
        "original_document_id": "original",
        "analysis_document_id": "analysis",
    }
    expected = {
        **{
            key: question[key]
            for key in (
                "id",
                "pair_id",
                "number",
                "source_question_range",
                "analysis_question_range",
            )
        },
        "original_document_id": "original",
        "analysis_document_id": "analysis",
        "source_stem_snapshot_sha256": evaluator.snapshot([stem]),
        "analysis_stem_snapshot_sha256": evaluator.snapshot([analysis_stem]),
        "answer_snapshot_sha256": evaluator.snapshot(answer),
    }
    manifest = {
        "denominator": 1,
        "minimum_confirmed": 1,
        "minimum_rate": 1.0,
        "questions": [expected],
        "sources": sources,
    }
    bank = {"documents": documents, "pairs": [pair], "questions": [question]}

    (tmp_path / "bank.json").write_text(json.dumps(bank))
    assert evaluator.evaluate(tmp_path, manifest)["passed"]

    _png(tmp_path / "media/original.png", (250, 20, 30, 255))
    (tmp_path / "bank.json").write_text(json.dumps(bank))
    result = evaluator.evaluate(tmp_path, manifest)
    assert not result["passed"]
    assert result["failed"] == 1
    assert "rendered image differs from source pixels" in result["questions"][0]["errors"]
