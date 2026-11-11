"""The local command must not mutate either input dataset through its output path."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_cli_rejects_unknown_layout_before_loading_or_writing(tmp_path):
    output = tmp_path / "should-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_bank_practice.py"),
            "--bank",
            str(tmp_path / "missing-bank"),
            "--semantic",
            str(tmp_path / "missing-semantic"),
            "--output",
            str(output),
            "--inventory-only",
            "--layout",
            "unknown",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("protected_name", ["source", "semantic"])
def test_cli_rejects_output_inside_source_before_loading(tmp_path, protected_name):
    source = tmp_path / "source"
    semantic = tmp_path / "semantic"
    source.mkdir()
    semantic.mkdir()
    sentinel = tmp_path / protected_name / "sentinel.txt"
    sentinel.write_text("keep")
    output = tmp_path / protected_name / "new-output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_bank_practice.py"),
            "--bank",
            str(source),
            "--semantic",
            str(semantic),
            "--output",
            str(output),
            "--inventory-only",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "output must be separate" in result.stderr
    assert not output.exists()
    assert sentinel.read_text() == "keep"
    assert list((tmp_path / protected_name).iterdir()) == [sentinel]


def test_cli_rejects_output_ancestor_of_source(tmp_path):
    source = tmp_path / "source"
    semantic = tmp_path / "semantic"
    source.mkdir()
    semantic.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_bank_practice.py"),
            "--bank",
            str(source),
            "--semantic",
            str(semantic),
            "--output",
            str(tmp_path),
            "--inventory-only",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "output must be separate" in result.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == ["semantic", "source"]
