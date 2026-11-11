"""Comparison publication is atomic; converter seams here are explicitly synthetic."""

import json
import runpy
import sys
from pathlib import Path

import pytest
from app.bank_practice_contracts import BankPracticeSnapshot, PracticeBank
from app.contracts import Curriculum, PracticeRequest, Quota, StudentProgress
from app.exporting import ExportError
from app.semantic_bank import canonical_hash

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failure", ["second_layout", "progress_change"])
def test_comparison_does_not_publish_partial_or_stale_progress(monkeypatch, tmp_path, failure):
    import app.source_exporting

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    curriculum = Curriculum.model_validate(
        {"version": "synthetic-layout", "points": [{"id": "p", "name": "Synthetic"}]}
    )
    bank = PracticeBank(
        source_bank_sha256="a" * 64,
        annotations_sha256="b" * 64,
        curriculum_sha256="c" * 64,
        curriculum_version=curriculum.version,
        curriculum_snapshot=curriculum,
        questions=[],
    )
    snapshot = BankPracticeSnapshot(
        is_synthetic_student=True,
        student_id="synthetic",
        progress_revision="v1",
        curriculum_version=curriculum.version,
        bank_sha256=canonical_hash(bank.model_dump(mode="json")),
        request=PracticeRequest(quotas=[Quota(point_id="p", count=0)]),
        items=[],
        shortfalls=[],
    )
    progress = StudentProgress(
        student_id="synthetic",
        revision="v1",
        curriculum_version=curriculum.version,
        learned_point_ids=["p"],
    )
    (inputs / "snapshot.json").write_text(snapshot.model_dump_json())
    original_snapshot = (inputs / "snapshot.json").read_bytes()
    progress_file = inputs / "progress.json"
    progress_file.write_text(progress.model_dump_json())
    (inputs / "curriculum.json").write_text(curriculum.model_dump_json())
    output = tmp_path / "comparison"
    calls = []

    def fake_export(_snapshot, destination, **kwargs):
        calls.append(kwargs["layout"])
        destination.mkdir()
        (destination / "synthetic-transaction-marker.txt").write_text("No real PDF created")
        if len(calls) == 2:
            if failure == "second_layout":
                raise ExportError("synthetic second-layout conversion failure")
            changed = json.loads(progress_file.read_text())
            changed["revision"] = "v2"
            progress_file.write_text(json.dumps(changed))
        return {
            "snapshot_id": canonical_hash(snapshot.model_dump(mode="json")),
            "counts": {},
            "files": {},
        }

    monkeypatch.setattr(app.source_exporting, "export_bank_snapshot", fake_export)
    namespace = runpy.run_path(str(ROOT / "scripts/export_practice_layouts.py"))
    main = namespace["main"]
    monkeypatch.setitem(main.__globals__, "load_practice_bank", lambda *_: bank)
    argv = ["export_practice_layouts.py"]
    for name, value in {
        "snapshot": inputs / "snapshot.json",
        "bank": inputs,
        "semantic": inputs,
        "progress": progress_file,
        "output": output,
        "soffice": tmp_path / "fake-soffice",
    }.items():
        argv.extend([f"--{name}", str(value)])
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises((ExportError, ValueError)):
        main()
    assert calls == ["answer_space", "compact"]
    assert not output.exists()
    assert not list(tmp_path.glob(".comparison-*"))
    assert (inputs / "snapshot.json").read_bytes() == original_snapshot
