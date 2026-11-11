from __future__ import annotations

import hashlib
import html
from pathlib import Path

from app.source_association import compare_association
from PIL import Image


def _block(text: str, markup: str | None = None) -> dict:
    return {
        "document_id": "synthetic-doc",
        "paragraph_index": 1,
        "text": text,
        "html": markup if markup is not None else f"<p>{html.escape(text)}</p>",
        "media_paths": [],
        "media_occurrences": [],
        "ole_occurrences": [],
        "omml_occurrences": [],
    }


def _write_image(root: Path, name: str, size: tuple[int, int], color: str) -> str:
    Image.new("RGBA", size, color).save(root / name)
    return name


def _add_media(
    block: dict,
    path: str,
    *,
    kind: str = "drawingml",
    ole_id: str | None = None,
    source_rel_id: str | None = None,
    native_payload: bytes | None = None,
    preview_rel_ids: list[str] | None = None,
    preview_media_paths: list[str] | None = None,
) -> None:
    occurrence = {
        "occurrence": len(block["media_occurrences"]) + 1,
        "paragraph_index": 1,
        "source_rel_id": source_rel_id or f"rel-{len(block['media_occurrences']) + 1}",
        "kind": kind,
        "ole_id": ole_id,
        "source_part": f"word/media/{Path(path).name}",
        "media_path": path,
        "rendered": True,
    }
    block["media_paths"].append(path)
    block["media_occurrences"].append(occurrence)
    block["html"] += f'<img src="{html.escape(path, quote=True)}"/>'
    if kind == "ole_preview":
        assert ole_id is not None and native_payload is not None
        block["ole_occurrences"].append(
            {
                "ole_id": ole_id,
                "source_part": f"word/embeddings/{ole_id}.bin",
                "preview_rel_ids": preview_rel_ids or [occurrence["source_rel_id"]],
                "preview_media_paths": preview_media_paths or [path],
                "native_equation": {
                    "status": "valid",
                    "method": "native_mtef_exact_v1",
                    "payload_sha256": hashlib.sha256(native_payload).hexdigest(),
                    "payload_size": len(native_payload),
                    "mtef_version": 5,
                    "ole_sha256": hashlib.sha256(b"cfb" + native_payload).hexdigest(),
                    "reason_code": None,
                },
            }
        )


def _compare(left: dict, right: dict, root: Path) -> tuple[str, dict]:
    return compare_association([left], [right], root)


def test_native_payload_identity_can_replace_different_preview_pixels(tmp_path: Path) -> None:
    left, right = _block("1. x+"), _block("1. x+")
    payload = b"complete native MTEF stream"
    _add_media(
        left,
        _write_image(tmp_path, "left.png", (4, 4), "black"),
        kind="ole_preview",
        ole_id="ole-left",
        native_payload=payload,
    )
    _add_media(
        right,
        _write_image(tmp_path, "right.png", (8, 8), "white"),
        kind="ole_preview",
        ole_id="ole-right",
        native_payload=payload,
    )

    status, evidence = _compare(left, right, tmp_path)

    assert status == "confirmed"
    assert evidence["image_pixels_match"] is False
    assert evidence["media_identity_match"] is True
    item = evidence["media_evidence"][0]
    assert item["method"] == "native_mtef_exact"
    assert item["matched"] is True
    assert item["native_evidence"]["preview_pixels_match"] is False
    assert item["native_evidence"]["source_preview"]["size"] == [4, 4]
    assert item["native_evidence"]["analysis_preview"]["size"] == [8, 8]


def test_different_valid_native_payload_never_uses_identical_preview_fallback(
    tmp_path: Path,
) -> None:
    left, right = _block("1. x"), _block("1. x")
    _add_media(
        left,
        _write_image(tmp_path, "left.png", (4, 4), "black"),
        kind="ole_preview",
        ole_id="ole-left",
        native_payload=b"formula one",
    )
    _add_media(
        right,
        _write_image(tmp_path, "right.png", (4, 4), "black"),
        kind="ole_preview",
        ole_id="ole-right",
        native_payload=b"formula two",
    )

    status, evidence = _compare(left, right, tmp_path)

    assert status == "unverified"
    assert evidence["media_evidence"][0]["method"] == "native_mtef_exact"
    assert evidence["media_evidence"][0]["reason_code"] == "native_payload_mismatch"


def test_ole_preview_rel_and_media_path_must_bind_to_ole_record(tmp_path: Path) -> None:
    left, right = _block("1. x"), _block("1. x")
    _add_media(
        left,
        _write_image(tmp_path, "left.png", (4, 4), "black"),
        kind="ole_preview",
        ole_id="ole-left",
        native_payload=b"same",
    )
    _add_media(
        right,
        _write_image(tmp_path, "right.png", (4, 4), "black"),
        kind="ole_preview",
        ole_id="ole-right",
        native_payload=b"same",
        preview_rel_ids=["wrong-rel"],
    )

    status, evidence = _compare(left, right, tmp_path)

    assert status == "unverified"
    assert evidence["media_evidence"][0]["method"] == "unmatched"
    assert evidence["media_evidence"][0]["reason_code"] == "ole_preview_relationship_unbound"


def test_plain_media_fallback_requires_exact_rgba_and_dimension_match(tmp_path: Path) -> None:
    left, right = _block("1. diagram"), _block("1. diagram")
    _add_media(left, _write_image(tmp_path, "left.png", (3, 3), "black"))
    _add_media(right, _write_image(tmp_path, "right.png", (3, 3), "black"))

    status, evidence = _compare(left, right, tmp_path)
    assert status == "confirmed"
    assert evidence["media_evidence"][0]["method"] == "exact_rgba"
    assert evidence["media_evidence"][0]["matched"] is True

    Image.new("RGBA", (6, 6), "black").save(tmp_path / "right.png")
    status, evidence = _compare(left, right, tmp_path)
    assert status == "unverified"
    assert evidence["image_pixels_match"] is False


def test_text_normalization_preserves_superscripts_and_decimal_points(tmp_path: Path) -> None:
    left = _block("1. x²=3.14")
    same = _block("１、 x² = 3.14")
    changed_power = _block("１、 x2 = 3.14")

    assert _compare(left, same, tmp_path)[0] == "confirmed"
    assert _compare(left, changed_power, tmp_path)[0] == "unverified"


def test_media_position_and_table_shape_are_part_of_ordered_structure(tmp_path: Path) -> None:
    left = _block("1. x+", '<p>1. x<img src="left.png"/>+</p>')
    right = _block("1. x+", '<p>1. x+<img src="right.png"/></p>')
    _add_media(left, _write_image(tmp_path, "left.png", (3, 3), "black"))
    _add_media(right, _write_image(tmp_path, "right.png", (3, 3), "black"))
    assert _compare(left, right, tmp_path)[0] == "unverified"

    table_a = _block("1. AB", "<p>1.</p><table><tr><td>A</td><td>B</td></tr></table>")
    table_b = _block("1. AB", "<p>1.</p><table><tr><td>A</td></tr><tr><td>B</td></tr></table>")
    assert _compare(table_a, table_b, tmp_path)[0] == "unverified"
