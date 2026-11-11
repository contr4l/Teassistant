"""Independent association acceptance using only synthetic text/images/CFB bytes."""

import hashlib
import html
import io
import struct

import olefile
import pytest
from PIL import Image


def synthetic_cfb(stream, *, stream_name="Equation Native"):
    """Make a minimal CFB mini stream, independent of the production extractor."""
    assert 0 < len(stream) <= 512
    free, end, fat = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, 2, 1, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    fat_sector = struct.pack("<128I", fat, end, end, end, *([free] * 124))

    def directory_entry(name, kind, start, size, child=free):
        entry = bytearray(128)
        encoded = (name + "\0").encode("utf-16-le")
        entry[: len(encoded)] = encoded
        struct.pack_into("<HBBIII", entry, 64, len(encoded), kind, 1, free, free, child)
        struct.pack_into("<IQ", entry, 116, start, size)
        return bytes(entry)

    mini_count = (len(stream) + 63) // 64
    directory = directory_entry("Root Entry", 5, 3, mini_count * 64, child=1)
    directory += directory_entry(stream_name, 2, 0, len(stream)) + bytes(256)
    mini_chain = [i + 1 for i in range(mini_count)]
    mini_chain[-1] = end
    mini_fat = struct.pack("<128I", *mini_chain, *([free] * (128 - mini_count)))
    result = bytes(header) + fat_sector + directory + mini_fat + stream.ljust(512, b"\0")
    with olefile.OleFileIO(io.BytesIO(result)) as container:
        assert container.openstream(stream_name).read() == stream
    return result


def synthetic_payload(body=b"\x01\x00"):
    # A recognized MTEF header plus opaque fixture bytes; no AST validity claim.
    return b"\x05\x01\x00\x07\x08DSMT7\x00\x00" + body + b"\x00"


def native_stream(payload, *, declared_size=None, header_size=28):
    wrapper = bytearray(28)
    struct.pack_into("<I", wrapper, 0, header_size)
    struct.pack_into("<H", wrapper, 4, 2)
    struct.pack_into("<H", wrapper, 6, 1)
    struct.pack_into("<I", wrapper, 8, len(payload) if declared_size is None else declared_size)
    return bytes(wrapper) + payload


def extract(raw):
    from app.native_equation import extract_native_equation

    return extract_native_equation(raw)


def test_complete_payload_identity_includes_every_opaque_record_byte():
    payload = synthetic_payload(b"\xfeopaque-unknown-record\x00")
    raw = synthetic_cfb(native_stream(payload))
    result = extract(raw)
    assert result["status"] == "valid"
    assert result["method"] == "native_mtef_exact_v1"
    assert result["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["payload_size"] == len(payload)
    assert result["mtef_version"] == 5
    assert result["ole_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["reason_code"] is None


def test_same_payload_with_different_wrapper_metadata_preserves_identity():
    payload = synthetic_payload()
    first = native_stream(payload)
    second = bytearray(first)
    second[12] = 1
    a, b = extract(synthetic_cfb(first)), extract(synthetic_cfb(bytes(second)))
    assert a["status"] == b["status"] == "valid"
    assert a["payload_sha256"] == b["payload_sha256"]
    assert a["ole_sha256"] != b["ole_sha256"]


@pytest.mark.parametrize(
    "mutation",
    ["truncated", "extra_tail", "wrong_header", "bad_version", "missing_end", "empty_payload"],
)
def test_invalid_native_container_header_or_length_is_unavailable(mutation):
    payload = synthetic_payload()
    stream = native_stream(payload)
    if mutation == "truncated":
        stream = stream[:-1]
    elif mutation == "extra_tail":
        stream += b"\x00"
    elif mutation == "wrong_header":
        stream = native_stream(payload, header_size=24)
    elif mutation == "missing_end":
        stream = native_stream(payload[:-1] + b"\x7f")
    elif mutation == "empty_payload":
        stream = native_stream(b"")
    else:
        stream = native_stream(bytes([99]) + payload[1:])
    result = extract(synthetic_cfb(stream))
    assert result["status"] == "unavailable"
    assert result["reason_code"]
    assert result["payload_sha256"] is None
    assert result["payload_size"] is None
    assert result["mtef_version"] is None


def test_missing_equation_stream_and_non_cfb_bytes_are_unavailable():
    for raw in (
        b"synthetic-not-a-CFB",
        synthetic_cfb(native_stream(synthetic_payload()), stream_name="Other"),
    ):
        result = extract(raw)
        assert result["status"] == "unavailable"
        assert result["payload_sha256"] is None


def signature(payload):
    result = extract(synthetic_cfb(native_stream(payload)))
    assert result["status"] == "valid"
    assert result["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    return result


def block(text="1. 合成题干", *, markup=None):
    return {
        "document_id": "synthetic-doc",
        "paragraph_index": 1,
        "source_paragraph_indices": [1],
        "text": text,
        "html": markup if markup is not None else "<p>" + html.escape(text) + "</p>",
        "media_paths": [],
        "media_occurrences": [],
        "ole_occurrences": [],
        "omml_occurrences": [],
    }


def add_image(block, root, filename, *, payload=None, color="black", size=(3, 3)):
    Image.new("RGBA", size, color).save(root / filename)
    number = len(block["media_occurrences"]) + 1
    rid, oid = f"preview-{number}", f"object-{number}"
    occurrence = {
        "occurrence": number,
        "paragraph_index": 1,
        "source_rel_id": rid,
        "kind": "ole_preview" if payload is not None else "drawingml",
        "ole_id": oid if payload is not None else None,
        "source_part": f"word/media/{filename}",
        "media_path": filename,
        "rendered": True,
    }
    block["media_paths"].append(filename)
    block["media_occurrences"].append(occurrence)
    block["html"] += f'<img src="{filename}"/>'
    if payload is not None:
        block["ole_occurrences"].append(
            {
                "ole_id": oid,
                "ole_rel_id": f"ole-rel-{number}",
                "source_part": f"word/embeddings/{oid}.bin",
                "preview_rel_ids": [rid],
                "preview_media_paths": [filename],
                "native_equation": signature(payload),
            }
        )


def compare(first, second, root):
    from app.source_association import compare_association

    return compare_association([first], [second], root)


def test_same_native_payload_with_different_preview_dimensions_is_source_identity(tmp_path):
    first, second = block(), block()
    payload = synthetic_payload(b"\xfeall-records-retained")
    add_image(first, tmp_path, "a.png", payload=payload, size=(3, 3))
    add_image(second, tmp_path, "b.png", payload=payload, size=(6, 6))
    status, evidence = compare(first, second, tmp_path)
    assert status == "confirmed"
    assert evidence["image_pixels_match"] is False
    assert evidence["media_identity_match"] is True
    assert any(
        item["method"].startswith("native_mtef_exact") and item["matched"]
        for item in evidence["media_evidence"]
    )


@pytest.mark.parametrize(
    "change",
    [
        b"number-3",
        b"decimal-1.2",
        b"negative-sign",
        b"denominator",
        b"subscript",
        b"superscript",
        b"relation",
        b"\xfeunknown-byte",
    ],
)
def test_different_native_payload_never_falls_back_to_identical_preview(tmp_path, change):
    first, second = block(), block()
    add_image(first, tmp_path, "a.png", payload=synthetic_payload(b"baseline"))
    add_image(second, tmp_path, "b.png", payload=synthetic_payload(change))
    assert compare(first, second, tmp_path)[0] == "unverified"


@pytest.mark.parametrize(
    "first_text,second_text",
    [
        ("1. 数字12", "1. 数字13"),
        ("1. 长度1.2", "1. 长度12"),
        ("1. x+2", "1. x-2"),
        ("1. x-2", "1. x2"),
        ("1. x²", "1. x2"),
        ("1. x₂", "1. x2"),
        ("1. x<2", "1. x>2"),
    ],
)
def test_text_mathematical_changes_are_not_normalized_away(tmp_path, first_text, second_text):
    assert compare(block(first_text), block(second_text), tmp_path)[0] == "unverified"


def test_same_native_formula_does_not_exempt_a_changed_geometry_image(tmp_path):
    first, second = block(), block()
    payload = synthetic_payload()
    add_image(first, tmp_path, "formula-a.png", payload=payload)
    add_image(second, tmp_path, "formula-b.png", payload=payload, size=(6, 6))
    add_image(first, tmp_path, "figure-a.png", color="white")
    add_image(second, tmp_path, "figure-b.png", color="white")
    with Image.open(tmp_path / "figure-b.png") as original:
        changed = original.copy()
    changed.putpixel((1, 1), (0, 0, 0, 255))
    changed.save(tmp_path / "figure-b.png")
    assert compare(first, second, tmp_path)[0] == "unverified"


@pytest.mark.parametrize("other_size", [(3, 3), (6, 6)])
def test_missing_ole_preview_binding_cannot_claim_native_identity(tmp_path, other_size):
    first, second = block(), block()
    add_image(first, tmp_path, "a.png", payload=synthetic_payload())
    add_image(second, tmp_path, "b.png", payload=synthetic_payload(), size=other_size)
    second["media_occurrences"][0]["ole_id"] = None
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_native_identity_does_not_exempt_a_missing_preview_file(tmp_path):
    first, second = block(), block()
    add_image(first, tmp_path, "a.png", payload=synthetic_payload())
    add_image(second, tmp_path, "b.png", payload=synthetic_payload())
    (tmp_path / "b.png").unlink()
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_native_identity_without_any_bound_visible_preview_is_unverified(tmp_path):
    first, second = block(), block()
    for item, filename in [(first, "a.png"), (second, "b.png")]:
        add_image(item, tmp_path, filename, payload=synthetic_payload())
        item["media_occurrences"] = []
        item["media_paths"] = []
        item["html"] = "<p>1. 合成题干<span>[嵌入公式预览缺失]</span></p>"
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_media_order_changes_are_not_hidden_by_equal_native_sets(tmp_path):
    first, second = block(), block()
    add_image(first, tmp_path, "a1.png", payload=synthetic_payload(b"formula-A"))
    add_image(first, tmp_path, "a2.png", payload=synthetic_payload(b"formula-B"))
    add_image(second, tmp_path, "b1.png", payload=synthetic_payload(b"formula-B"))
    add_image(second, tmp_path, "b2.png", payload=synthetic_payload(b"formula-A"))
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_native_formula_position_among_text_cannot_be_discarded(tmp_path):
    first, second = block("1. x+"), block("1. x+")
    add_image(first, tmp_path, "a.png", payload=synthetic_payload())
    add_image(second, tmp_path, "b.png", payload=synthetic_payload())
    first["html"] = '<p>1. x<img src="a.png"/>+</p>'
    second["html"] = '<p>1. x+<img src="b.png"/></p>'
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_table_row_column_change_with_identical_text_is_not_confirmed(tmp_path):
    first = block("1. AB", markup="<p>1.</p><table><tr><td>A</td><td>B</td></tr></table>")
    second = block("1. AB", markup="<p>1.</p><table><tr><td>A</td></tr><tr><td>B</td></tr></table>")
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_mathml_subscript_and_superscript_are_distinct_with_identical_text(tmp_path):
    first = block("1. x2", markup="<p>1.</p><math><msub><mi>x</mi><mn>2</mn></msub></math>")
    second = block("1. x2", markup="<p>1.</p><math><msup><mi>x</mi><mn>2</mn></msup></math>")
    assert compare(first, second, tmp_path)[0] == "unverified"


def test_identical_table_and_plain_geometry_are_positive_cases(tmp_path):
    markup = "<p>1.</p><table><tr><td>A</td><td>B</td></tr></table>"
    first, second = block("1. AB", markup=markup), block("1. AB", markup=markup)
    add_image(first, tmp_path, "figure-a.png")
    add_image(second, tmp_path, "figure-b.png")
    assert compare(first, second, tmp_path)[0] == "confirmed"
