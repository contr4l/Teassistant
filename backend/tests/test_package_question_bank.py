"""Synthetic packaging tests; only local fixture files are copied."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.bank_practice import load_practice_bank
from test_bank_practice import _fixture

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/package_question_bank.py"
_SPEC = importlib.util.spec_from_file_location("package_question_bank", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
PackageError = _MODULE.PackageError
package_question_bank = _MODULE.package_question_bank


def _annotations_path(semantic_dir: Path) -> Path:
    return semantic_dir / "annotations.json"


def _change_run_path(semantic_dir: Path, new_path: str) -> bytes:
    path = _annotations_path(semantic_dir)
    original = path.read_bytes()
    annotations = json.loads(original)
    question = next(iter(annotations["questions"].values()))
    question["provenance"]["source_run_path"] = new_path
    path.write_text(json.dumps(annotations, ensure_ascii=False, indent=2), encoding="utf-8")
    return original


def test_loader_accepts_relative_package_path_and_legacy_absolute_path(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    assert load_practice_bank(bank_dir, semantic_dir).questions[0].status == "eligible"
    _change_run_path(semantic_dir, "source-run")
    bank = load_practice_bank(bank_dir, semantic_dir)
    assert bank.questions[0].status == "eligible"


@pytest.mark.parametrize("bad_path", ["../outside", "source-run\\calls", "/tmp/outside-run"])
def test_loader_blocks_traversal_backslash_and_external_absolute_run_path(tmp_path, bad_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    _change_run_path(semantic_dir, bad_path)
    bank = load_practice_bank(bank_dir, semantic_dir)
    assert bank.questions[0].status == "blocked"
    assert "semantic_source_run_unavailable" in bank.questions[0].block_reasons


def test_loader_blocks_relative_symlink_that_escapes_semantic_parent(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    outside = tmp_path.parent / f"outside-run-{tmp_path.name}"
    outside.mkdir()
    (outside / "run.json").write_text("{}", encoding="utf-8")
    (outside / "calls/0001").mkdir(parents=True)
    (semantic_dir.parent / "escaped-run").symlink_to(outside, target_is_directory=True)
    _change_run_path(semantic_dir, "escaped-run")
    bank = load_practice_bank(bank_dir, semantic_dir)
    assert bank.questions[0].status == "blocked"
    assert "semantic_source_run_unavailable" in bank.questions[0].block_reasons


def test_cli_packages_evidence_relatively_and_preserves_source_bytes(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    annotations_before = _annotations_path(semantic_dir).read_bytes()
    bank_before = (bank_dir / "bank.json").read_bytes()
    curriculum_before = (semantic_dir / "curriculum.json").read_bytes()
    call_before = {
        name: (tmp_path / "source-run/calls/0001" / name).read_bytes()
        for name in ("request.json", "prompt.txt", "result.json")
    }
    output = tmp_path / "deployment-package"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bank-dir",
            str(bank_dir),
            "--semantic-dir",
            str(semantic_dir),
            "--output-dir",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    cli_summary = json.loads(result.stdout)
    assert cli_summary["question_count"] == 1
    assert cli_summary["eligible_count"] == 1
    packaged_annotations = json.loads((output / "semantic/annotations.json").read_bytes())
    annotation = next(iter(packaged_annotations["questions"].values()))
    run_path = annotation["provenance"]["source_run_path"]
    assert run_path.startswith("evidence/runs/")
    assert not Path(run_path).is_absolute()
    for filename, original in call_before.items():
        assert (output / run_path / "calls/0001" / filename).read_bytes() == original
    assert (output / "source/bank.json").read_bytes() == bank_before
    assert (output / "semantic/curriculum.json").read_bytes() == curriculum_before
    assert _annotations_path(semantic_dir).read_bytes() == annotations_before
    assert (
        load_practice_bank(output / "source", output / "semantic").questions[0].status == "eligible"
    )
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["annotations_source_sha256"] == hashlib.sha256(annotations_before).hexdigest()
    assert (
        manifest["annotations_packaged_sha256"]
        == hashlib.sha256((output / "semantic/annotations.json").read_bytes()).hexdigest()
    )
    assert manifest["inventory_before"]["questions"] == manifest["inventory_after"]["questions"]
    assert manifest["source_run_mappings"][0]["calls"][0]["files"]["result.json"]


def test_packaging_preserves_legitimate_blocked_warning_rows(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path, warning="synthetic warning")
    output = tmp_path / "package-with-warning"
    manifest = package_question_bank(bank_dir, semantic_dir, output)
    assert manifest["inventory_before"]["blocked_count"] == 1
    assert manifest["inventory_after"]["questions"] == manifest["inventory_before"]["questions"]


def test_tampered_call_digest_is_rejected_without_publishing(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    result_path = tmp_path / "source-run/calls/0001/result.json"
    result_path.write_bytes(result_path.read_bytes() + b" ")
    output = tmp_path / "tampered-package"
    with pytest.raises(PackageError):
        package_question_bank(bank_dir, semantic_dir, output)
    assert not output.exists()


def test_native_source_media_drift_is_rejected_without_publishing(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    media_path = bank_dir / "media/doc-original/image1.png"
    media_path.write_bytes(media_path.read_bytes() + b"changed")
    output = tmp_path / "tampered-media-package"
    with pytest.raises(PackageError):
        package_question_bank(bank_dir, semantic_dir, output)
    assert not output.exists()


def test_output_cannot_overwrite_or_live_inside_inputs(tmp_path):
    bank_dir, semantic_dir, _curriculum, _question = _fixture(tmp_path)
    with pytest.raises(PackageError):
        package_question_bank(bank_dir, semantic_dir, bank_dir / "package")
    output = tmp_path / "already-exists"
    output.mkdir()
    (output / "sentinel").write_text("preserve", encoding="utf-8")
    with pytest.raises(PackageError):
        package_question_bank(bank_dir, semantic_dir, output)
    assert (output / "sentinel").read_text(encoding="utf-8") == "preserve"
