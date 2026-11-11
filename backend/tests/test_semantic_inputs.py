from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from app import semantic_inputs


def _question(qid: str, *, association: str = "confirmed") -> dict:
    return {
        "id": qid,
        "association_status": association,
        "stem_blocks": [{"text": "题干 x²", "html": "<p>题干 x<sup>2</sup></p>"}],
        "answer_blocks": [{"text": "答案 4", "html": "<p>答案 4</p>"}],
        "analysis_blocks": [{"text": "解析过程", "html": "<p>解析过程</p>"}],
    }


def _bank(tmp_path: Path, questions: list[dict]) -> Path:
    root = tmp_path / "bank"
    root.mkdir()
    (root / "bank.json").write_text(
        json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _fake_render(*args, output_dir: Path, qindex: int) -> list[dict]:
    from PIL import Image

    image = output_dir / f"q{qindex:03d}-tile001.png"
    Image.new("RGB", (1100, 600), "white").save(image, format="PNG")
    return [
        {
            "path": image.relative_to(output_dir.parent).as_posix(),
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "width": 1100,
            "height": 600,
        }
    ]


def _stub_browser(monkeypatch):
    monkeypatch.setattr(semantic_inputs, "_browser_page", lambda executable: nullcontext(object()))
    monkeypatch.setattr(semantic_inputs, "_render_page", _fake_render)


def test_manifest_hash_and_unconfirmed_answer_exclusion(tmp_path, monkeypatch):
    bank = _bank(tmp_path, [_question("q1", association="unverified")])
    _stub_browser(monkeypatch)
    output = tmp_path / "rendered"

    manifest = semantic_inputs.render_inputs(bank, output, ["q1"])

    record = manifest["questions"][0]
    assert (
        record["source_hash"]
        == hashlib.sha256(
            json.dumps(
                _question("q1", association="unverified"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert record["association_status"] == "unverified"
    assert record["reliable_solution_used"] is False
    assert record["text"] == "【题目】\n题干 x²"
    assert (
        manifest["source_bank_sha256"]
        == hashlib.sha256((bank / "bank.json").read_bytes()).hexdigest()
    )
    assert json.loads((output / "inputs.json").read_text())["questions"][0]["question_id"] == "q1"
    assert record["images"][0]["path"].startswith("images/")


def test_rejects_remote_and_symlink_escape_media(tmp_path):
    bank = _bank(tmp_path, [_question("q1")])
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (bank / "media").mkdir()
    (bank / "media" / "link.png").symlink_to(outside)

    with pytest.raises(ValueError, match="local bank media"):
        semantic_inputs.safe_fragment('<img src="https://example.test/x.png">', bank)
    with pytest.raises(ValueError, match="escapes"):
        semantic_inputs.safe_fragment('<img src="media/link.png">', bank)
    with pytest.raises(ValueError, match="unsupported markup"):
        semantic_inputs.safe_fragment("<script>alert(1)</script>", bank)


def test_embeds_only_verified_raster_source_media(tmp_path):
    from PIL import Image

    bank = _bank(tmp_path, [_question("q1")])
    media = bank / "media"
    media.mkdir()
    Image.new("RGB", (8, 6), "white").save(media / "diagram.png")

    safe = semantic_inputs.safe_fragment('<img src="media/diagram.png" width="8" height="6">', bank)

    assert 'src="data:image/png;base64,' in safe
    assert "file://" not in safe


def test_requires_full_fixed_batch_and_fresh_output(tmp_path, monkeypatch):
    bank = _bank(tmp_path, [_question("q1")])
    with pytest.raises(ValueError, match="exactly 200"):
        semantic_inputs.render_inputs(bank, tmp_path / "full")

    _stub_browser(monkeypatch)
    output = tmp_path / "subset"
    semantic_inputs.render_inputs(bank, output, ["q1"])
    with pytest.raises(FileExistsError, match="fresh"):
        semantic_inputs.render_inputs(bank, output, ["q1"])


def test_browser_failure_does_not_publish_partial_output(tmp_path, monkeypatch):
    bank = _bank(tmp_path, [_question("q1")])

    def fail_render(*args, **kwargs):
        raise RuntimeError("browser failed")

    monkeypatch.setattr(semantic_inputs, "_browser_page", lambda executable: nullcontext(object()))
    monkeypatch.setattr(semantic_inputs, "_render_page", fail_render)
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="browser failed"):
        semantic_inputs.render_inputs(bank, output, ["q1"])
    assert not output.exists()
    assert not list(tmp_path.glob(".failed.stage-*"))


def test_tiling_preserves_tail_and_never_exceeds_tile_height(tmp_path):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1100, 5001), color="white").save(buf, format="PNG")
    parent = tmp_path / "stage"
    parent.mkdir()
    images = parent / "images"
    images.mkdir()

    tiles = semantic_inputs._tiles(buf.getvalue(), images, 1)

    assert len(tiles) == 3
    assert all(0 < item["height"] <= 2400 for item in tiles)
    assert [(item["source_y_start"], item["source_y_end"]) for item in tiles] == [
        (0, 2400),
        (2320, 4720),
        (4640, 5001),
    ]
    assert all(item["source_height"] == 5001 for item in tiles)
    assert tiles[-1]["source_y_end"] == tiles[-1]["source_height"]
    for item in tiles:
        path = parent / item["path"]
        with Image.open(path) as image:
            image.verify()
