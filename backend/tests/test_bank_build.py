"""Publication failure boundaries, using synthetic data only."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[2] / "scripts/build_question_bank.py"
    spec = importlib.util.spec_from_file_location("build_question_bank", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_bank_is_not_changed(builder, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "bank"
    output.mkdir()
    marker = output / "existing.txt"
    marker.write_text("existing library")
    with pytest.raises(ValueError, match="exists"):
        builder.build(source, output)
    assert marker.read_text() == "existing library"


def test_failed_import_does_not_publish_partial_bank(builder, monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "bank"

    def fail_after_partial_write(source, working):
        working.mkdir(parents=True)
        (working / "partial.json").write_text("incomplete")
        raise ValueError("synthetic missing equation preview")

    monkeypatch.setattr(builder, "build_source_bank", fail_after_partial_write)
    with pytest.raises(ValueError, match="synthetic missing"):
        builder.build(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".question-bank-build-*"))


def test_fingerprint_preserves_table_layout_and_merges(builder, tmp_path):
    layouts = [
        "<table><tr><td>x</td><td>y</td></tr><tr><td>1</td><td>2</td></tr></table>",
        "<table><tr><td>x</td><td>y</td><td>1</td><td>2</td></tr></table>",
        '<table><tr><td colspan="2">x</td><td>y</td><td>1</td><td>2</td></tr></table>',
    ]
    bank = {
        "questions": [
            {
                "id": f"synthetic-{i}",
                "stem_blocks": [{"html": f"<p>{i + 1}. 合成题目</p>"}, {"html": layout}],
            }
            for i, layout in enumerate(layouts)
        ]
    }
    builder.add_exact_fingerprints(bank, tmp_path)
    assert len({q["content_fingerprint"] for q in bank["questions"]}) == 3
    assert bank["exact_duplicate_groups"] == []


def test_fingerprint_retains_math_structure_and_does_not_hash_empty_stems(builder, tmp_path):
    bank = {
        "questions": [
            {
                "id": "fraction",
                "stem_blocks": [{"html": "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"}],
            },
            {
                "id": "power",
                "stem_blocks": [{"html": "<math><msup><mn>1</mn><mn>2</mn></msup></math>"}],
            },
            {"id": "empty-1", "stem_blocks": []},
            {"id": "empty-2", "stem_blocks": []},
        ]
    }
    builder.add_exact_fingerprints(bank, tmp_path)
    assert (
        bank["questions"][0]["content_fingerprint"] != bank["questions"][1]["content_fingerprint"]
    )
    assert all(q["content_fingerprint"] is None for q in bank["questions"][2:])
    assert bank["exact_duplicate_groups"] == []


def test_fingerprint_ignores_only_the_leading_question_number(builder, tmp_path):
    bank = {
        "questions": [
            {
                "id": str(n),
                "stem_blocks": [{"html": f"<p>{n}. 合成题<math><mn>1.25</mn></math></p>"}],
            }
            for n in [1, 2]
        ]
    }
    builder.add_exact_fingerprints(bank, tmp_path)
    assert bank["exact_duplicate_groups"] == [["1", "2"]]


def test_fingerprint_does_not_strip_a_decimal_inside_math(builder, tmp_path):
    bank = {
        "questions": [
            {"id": value, "stem_blocks": [{"html": f"<math><mn>{value}</mn></math>"}]}
            for value in ["1.25", "25"]
        ]
    }
    builder.add_exact_fingerprints(bank, tmp_path)
    assert bank["exact_duplicate_groups"] == []


@pytest.mark.parametrize("status,exit_code", [("succeeded", 0), ("partial", 3), ("failed", 1)])
def test_cli_reports_partial_and_failed_batches(
    builder, monkeypatch, capsys, tmp_path, status, exit_code
):
    monkeypatch.setattr(builder, "build", lambda *args: {"batch_status": status})
    monkeypatch.setattr("sys.argv", ["build_question_bank", "--source", str(tmp_path)])
    assert builder.main() == exit_code
    assert status in capsys.readouterr().out
