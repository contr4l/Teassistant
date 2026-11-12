"""Independent portable-package checks with a synthetic source bank and real loader."""

import hashlib
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path, PurePosixPath

import pytest
from app.bank_practice import load_practice_bank
from test_bank_practice import _fixture

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/package_question_bank.py"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root):
    return {str(path.relative_to(root)): _sha(path) for path in root.rglob("*") if path.is_file()}


def _read(path):
    return json.loads(path.read_bytes())


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(bank, semantic, output):
    assert SCRIPT.is_file(), "packaging implementation must exist before negative checks"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bank-dir",
            str(bank),
            "--semantic-dir",
            str(semantic),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def source(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    bank, semantic, _, question = _fixture(original)
    assert load_practice_bank(bank, semantic).questions[0].status == "eligible"
    return original, bank, semantic, question["id"]


def test_package_is_self_contained_after_originals_disappear_and_package_moves(source, tmp_path):
    original, bank, semantic, qid = source
    (original / "source-run/.env").write_text("SYNTHETIC_UNREFERENCED_SECRET=not-real")
    before = _tree(original)
    original_annotations = _read(semantic / "annotations.json")
    output = tmp_path / "package"
    result = _run(bank, semantic, output)
    assert result.returncode == 0, result.stderr
    assert _tree(original) == before
    assert (output / "source/bank.json").read_bytes() == (bank / "bank.json").read_bytes()
    assert (output / "semantic/curriculum.json").read_bytes() == (
        semantic / "curriculum.json"
    ).read_bytes()
    packaged = _read(output / "semantic/annotations.json")
    expected = deepcopy(original_annotations)
    for question_id, row in packaged["questions"].items():
        reference = row["provenance"]["source_run_path"]
        path = PurePosixPath(reference)
        assert not path.is_absolute() and ".." not in path.parts and "\\" not in reference
        assert path.parts[:2] == ("evidence", "runs")
        expected["questions"][question_id]["provenance"]["source_run_path"] = reference
        assert _sha(output / reference / "run.json") == _sha(original / "source-run/run.json")
        for name in ("request.json", "prompt.txt", "result.json"):
            assert _sha(output / reference / "calls/0001" / name) == _sha(
                original / "source-run/calls/0001" / name
            )
    assert (
        packaged == expected
    )  # Path relocation alone cannot rewrite assessments or their evidence.
    assert not list(output.rglob(".env"))
    manifest = _read(output / "manifest.json")
    assert manifest["source_bank_sha256"] == _sha(bank / "bank.json")
    assert manifest["annotations_source_sha256"] == _sha(semantic / "annotations.json")
    assert manifest["annotations_packaged_sha256"] == _sha(output / "semantic/annotations.json")
    listed = set()
    for row in manifest["files"]:
        assert not PurePosixPath(row["path"]).is_absolute()
        assert ".." not in PurePosixPath(row["path"]).parts
        assert _sha(output / row["path"]) == row["sha256"]
        listed.add(row["path"])
    assert set(_tree(output)) - {"manifest.json"} <= listed
    original.rename(tmp_path / "originals-offline")
    relocated = tmp_path / "relocated-package"
    output.rename(relocated)
    loaded = load_practice_bank(relocated / "source", relocated / "semantic")
    assert [(row.id, row.status, row.block_reasons) for row in loaded.questions] == [
        (qid, "eligible", [])
    ]


@pytest.mark.parametrize("relative", ["../source-run", "", "evidence\\run"])
def test_loader_rejects_unsafe_relative_evidence_paths(source, tmp_path, relative):
    _original, bank, semantic, qid = source
    annotations = _read(semantic / "annotations.json")
    annotations["questions"][qid]["provenance"]["source_run_path"] = relative
    _write(semantic / "annotations.json", annotations)
    loaded = load_practice_bank(bank, semantic)
    assert loaded.questions[0].status == "blocked"
    assert "semantic_source_run_unavailable" in loaded.questions[0].block_reasons
    output = tmp_path / "bad-package"
    assert _run(bank, semantic, output).returncode != 0
    assert not output.exists()


def test_packager_does_not_follow_source_or_evidence_symlinks_outside_input(source, tmp_path):
    original, bank, semantic, qid = source
    outside = tmp_path / "outside-run"
    shutil.copytree(original / "source-run", outside)
    link = original / "outside-link"
    link.symlink_to(outside, target_is_directory=True)
    annotations = _read(semantic / "annotations.json")
    annotations["questions"][qid]["provenance"]["source_run_path"] = str(link)
    _write(semantic / "annotations.json", annotations)
    output = tmp_path / "bad-package"
    assert _run(bank, semantic, output).returncode != 0
    assert not output.exists()


@pytest.mark.parametrize(
    "damage",
    ["missing-call", "changed-call", "changed-media", "extra-source-symlink", "missing-provenance"],
)
def test_invalid_source_evidence_cannot_pass_by_preserving_the_same_blocked_count(
    source, tmp_path, damage
):
    original, bank, semantic, _qid = source
    if damage == "missing-call":
        (original / "source-run/calls/0001/result.json").unlink()
    elif damage == "changed-call":
        path = original / "source-run/calls/0001/result.json"
        result = _read(path)
        result["raw_json_result"]["assessments"][0]["difficulty_score"] = 56
        _write(path, result)
    elif damage == "changed-media":
        (bank / "media/doc-original/image1.png").write_bytes(b"synthetic-modified-media")
    elif damage == "extra-source-symlink":
        outside = tmp_path / "outside-secret.txt"
        outside.write_text("SYNTHETIC_NOT_PART_OF_SOURCE")
        (bank / "unreferenced-link.txt").symlink_to(outside)
    else:
        path = semantic / "annotations.json"
        annotations = _read(path)
        del annotations["questions"][_qid]["provenance"]
        _write(path, annotations)
    before = _tree(original)
    output = tmp_path / "bad-package"
    assert _run(bank, semantic, output).returncode != 0
    assert not output.exists()
    assert _tree(original) == before


def test_output_cannot_overwrite_existing_directory_or_be_nested_in_input(source, tmp_path):
    original, bank, semantic, _qid = source
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("synthetic existing output")
    before = _tree(original)
    assert _run(bank, semantic, existing).returncode != 0
    assert (existing / "keep.txt").read_text() == "synthetic existing output"
    for nested in (
        bank / "nested-package",
        semantic / "nested-package",
        original / "source-run/nested-package",
    ):
        assert _run(bank, semantic, nested).returncode != 0
        assert not nested.exists()
    assert _tree(original) == before


def test_loader_rechecks_call_bytes_even_if_attacker_rewrites_package_manifest(source, tmp_path):
    _original, bank, semantic, _qid = source
    output = tmp_path / "package"
    assert _run(bank, semantic, output).returncode == 0
    result_path = next((output / "evidence").rglob("result.json"))
    result_path.write_bytes(result_path.read_bytes() + b"\n")
    manifest = _read(output / "manifest.json")
    for row in manifest["files"]:
        if row["path"] == str(result_path.relative_to(output)):
            row["sha256"] = _sha(result_path)
    _write(output / "manifest.json", manifest)
    loaded = load_practice_bank(output / "source", output / "semantic")
    assert loaded.questions[0].status == "blocked"
    assert "semantic_call_digest_mismatch" in loaded.questions[0].block_reasons
