from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "diagnose_latex_bank", ROOT / "scripts" / "diagnose_latex_bank.py"
)
assert SPEC and SPEC.loader
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _block(*, with_formula: bool = False) -> dict:
    return {
        "document_id": "doc1",
        "paragraph_index": 1,
        "ole_occurrences": (
            [
                {
                    "source_part": "word/document.xml",
                    "ole_id": "ole1",
                    "ole_rel_id": "rId1",
                    "preview_rel_ids": [],
                    "preview_media_paths": [],
                }
            ]
            if with_formula
            else []
        ),
        "omml_occurrences": [],
        "media_occurrences": [],
    }


def _fake_converter(monkeypatch: pytest.MonkeyPatch) -> tuple[object, list[bytes]]:
    converted: list[bytes] = []
    module = SimpleNamespace(_CONVERTER="synthetic-v1", __file__=str(Path(__file__).resolve()))

    def convert(raw: bytes):
        converted.append(raw)
        return SimpleNamespace(
            status="converted",
            errors=[],
            model_dump=lambda mode="json": {
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "source_kind": "ole",
                "native_payload_sha256": "b" * 64,
                "status": "converted",
                "converter": "synthetic-v1",
                "errors": [],
            },
        )

    module.convert_ole_formula = convert
    module.convert_omml_formula = lambda xml: convert(xml.encode())
    module._sha = lambda raw: hashlib.sha256(raw).hexdigest()

    def validate(question, *_args):
        if question.id == "q2":
            error = ValueError("source binding failed")
            error.code = "source_document_sha_mismatch"
            raise error
        return {"doc1": object()}

    module._validate_source_binding = validate

    def occurrences(question, _docs):
        result = {}
        for field_blocks in question.source_payload.values():
            for block in field_blocks:
                for occurrence in block["ole_occurrences"]:
                    conversion = module.convert_ole_formula(b"same raw OLE")
                    result[module._sha(b"same raw OLE")] = conversion
        return result

    module._formula_occurrences = occurrences
    module._extract_occurrence_bytes = lambda *_args: b"same raw OLE"
    monkeypatch.setattr(diagnostic, "_load_converter", lambda _source: (module, None))
    return module, converted


def test_diagnose_keeps_raw_question_and_formula_denominators(tmp_path, monkeypatch):
    bank = tmp_path / "bank"
    semantic = tmp_path / "semantic"
    bank.mkdir()
    semantic.mkdir()
    rows = [
        {
            "id": "q1",
            "stem_blocks": [_block(with_formula=True)],
            "analysis_stem_blocks": [_block(with_formula=True)],
            "answer_blocks": [],
            "analysis_blocks": [],
        },
        {
            "id": "q2",
            "stem_blocks": [],
            "analysis_stem_blocks": [],
            "answer_blocks": [_block(with_formula=True)],
            "analysis_blocks": [],
        },
        {
            "id": "q3",
            "stem_blocks": [_block()],
            "analysis_stem_blocks": [],
            "answer_blocks": [],
            "analysis_blocks": [],
        },
    ]
    _write(bank / "bank.json", {"questions": rows, "documents": []})
    _write(semantic / "annotations.json", {})
    _write(semantic / "curriculum.json", {})
    _module, converted = _fake_converter(monkeypatch)
    monkeypatch.setattr(
        diagnostic,
        "load_practice_bank",
        lambda *_args: SimpleNamespace(
            questions=[
                SimpleNamespace(id="q1", status="eligible", asset_hashes={}),
                SimpleNamespace(id="q2", status="blocked", asset_hashes={}),
                SimpleNamespace(id="q3", status="eligible", asset_hashes={}),
            ]
        ),
    )

    report = diagnostic.diagnose(bank, semantic, None)
    summary = report["summary"]
    assert summary["question_denominators"]["raw_questions"] == 3
    assert summary["question_denominators"]["eligible_questions"] == 2
    assert summary["formula_denominators"]["diagnostic_total_occurrences"] == 3
    assert summary["formula_denominators"]["export_ole_occurrences"] == 2
    assert summary["formula_denominators"]["source_binding_failed_occurrences"] == 1
    assert len(report["occurrences"]) == 3
    assert len(converted) == 1  # One conversion for repeated identical raw OLE bytes.


def test_output_path_rejects_existing_and_nested_destinations(tmp_path):
    bank = tmp_path / "bank"
    semantic = tmp_path / "semantic"
    bank.mkdir()
    semantic.mkdir()
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="new"):
        diagnostic._check_output_path(existing, bank, semantic)
    with pytest.raises(ValueError, match="overlap"):
        diagnostic._check_output_path(bank / "report", bank, semantic)


def test_report_writer_publishes_complete_private_bundle(tmp_path):
    output = tmp_path / "report"
    report = {
        "summary": {"raw": 3},
        "formulas": [],
        "occurrences": [],
        "questions": [{"question_id": "synthetic-q"}],
    }
    diagnostic._write_report(report, output)
    assert {path.name for path in output.iterdir()} == {
        "summary.json",
        "formulas.json",
        "occurrences.json",
        "questions.json",
    }
    assert json.loads((output / "questions.json").read_text()) == report["questions"]
