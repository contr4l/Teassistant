import hashlib
import struct

import pytest
from app.native_equation import METHOD, extract_native_equation

_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD


def mtef5(records=b"\x00", *, app_version=7, subversion=8, options=0):
    key = f"DSMT{app_version}".encode("ascii") + b"\x00"
    return bytes([5, 1, 0, app_version, subversion]) + key + bytes([options]) + records


def native_stream(payload, *, cb_header=28, version=0x00020000, declared_size=None):
    if declared_size is None:
        declared_size = len(payload)
    header = struct.pack("<HIHI", cb_header, version, 0xC34B, declared_size)
    # Reserved wrapper fields vary among real historical files and are not MTEF.
    reserved = bytes.fromhex("7b88850024dd5e00d429e204ab12fda9")
    return header + reserved + payload


def _directory_entry(name, *, kind, child=_FREESECT, start=_ENDOFCHAIN, size=0, right=_FREESECT):
    entry = bytearray(128)
    encoded = name.encode("utf-16le") + b"\x00\x00"
    entry[: len(encoded)] = encoded
    struct.pack_into("<H", entry, 64, len(encoded))
    entry[66] = kind
    entry[67] = 1
    struct.pack_into("<III", entry, 68, _FREESECT, right, child)
    struct.pack_into("<I", entry, 116, start)
    struct.pack_into("<Q", entry, 120, size)
    return bytes(entry)


def compound_with_native(stream_bytes, *, stream_name="Equation Native"):
    """Build a small v3 CFB using a root mini stream and one miniFAT sector."""
    directory = bytearray(512)
    directory[:128] = _directory_entry("Root Entry", kind=5, child=1, start=2, size=512)
    directory[128:256] = _directory_entry(stream_name, kind=2, start=0, size=len(stream_bytes))
    mini_fat = struct.pack("<128I", _ENDOFCHAIN, *([_FREESECT] * 127))
    mini_stream = stream_bytes + bytes(512 - len(stream_bytes))
    fat_entries = [_FREESECT] * 128
    fat_entries[0] = _ENDOFCHAIN  # directory
    fat_entries[1] = _ENDOFCHAIN  # miniFAT
    fat_entries[2] = _ENDOFCHAIN  # root mini stream
    fat_entries[3] = _FATSECT
    fat = struct.pack("<128I", *fat_entries)

    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 40, 0)  # directory sectors (v3)
    struct.pack_into("<I", header, 44, 1)  # FAT sectors
    struct.pack_into("<I", header, 48, 0)  # first directory sector
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<II", header, 60, 1, 1)  # first/count miniFAT sectors
    struct.pack_into("<II", header, 68, _ENDOFCHAIN, 0)  # DIFAT start/count
    difat = [_FREESECT] * 109
    difat[0] = 3
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header) + bytes(directory) + mini_fat + mini_stream + fat


def extract(payload, **native_options):
    return extract_native_equation(compound_with_native(native_stream(payload, **native_options)))


def test_valid_native_payload_returns_exact_identity_and_nonzero_reserved_bytes_are_allowed():
    payload = mtef5()
    result = extract_native_equation(compound_with_native(native_stream(payload)))

    assert result == {
        "status": "valid",
        "method": METHOD,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_size": len(payload),
        "mtef_version": 5,
        "ole_sha256": result["ole_sha256"],
        "reason_code": None,
    }
    assert len(result["ole_sha256"]) == 64


def test_opaque_future_record_is_included_verbatim_in_payload_digest():
    payload = mtef5(b"\x66\x03xyz\x00")
    result = extract(payload)

    assert result["status"] == "valid"
    assert result["payload_size"] == len(payload)
    assert result["payload_sha256"] == hashlib.sha256(payload).hexdigest()


def test_payload_identity_does_not_normalize_any_mtef_header_or_record_bytes():
    first = extract(mtef5(b"\x00"))
    changed = extract(mtef5(b"\x00", subversion=9))

    assert first["status"] == changed["status"] == "valid"
    assert first["payload_sha256"] != changed["payload_sha256"]


@pytest.mark.parametrize(
    ("payload", "options", "expected_reason"),
    [
        (b"", {}, "mtef_header_truncated"),
        (bytes([4, 1, 0, 7, 8]) + b"DSMT7\0\0\0", {}, "mtef_version_unsupported"),
        (bytes([5, 0, 0, 7, 8]) + b"DSMT7\0\0\0", {}, "mtef_header_invalid"),
        (bytes([5, 1, 0, 8, 8]) + b"DSMT8\0\0\0", {}, "mtef_header_invalid"),
        (bytes([5, 1, 0, 7, 8]) + b"wrong\0\0\0", {}, "mtef_app_key_invalid"),
        (mtef5(options=2), {}, "mtef_options_invalid"),
        (mtef5(b""), {}, "mtef_records_missing"),
        (mtef5(b"\x01"), {}, "mtef_end_missing"),
        (mtef5(b"\x00\x7f"), {}, "mtef_end_missing"),
    ],
)
def test_invalid_or_truncated_mtef_is_unavailable(payload, options, expected_reason):
    result = extract(payload, **options)

    assert result["status"] == "unavailable"
    assert result["method"] == METHOD
    assert result["payload_sha256"] is None
    assert result["payload_size"] is None
    assert result["mtef_version"] is None
    assert result["reason_code"] == expected_reason
    assert len(result["ole_sha256"]) == 64


@pytest.mark.parametrize(
    ("native_bytes", "expected_reason"),
    [
        (native_stream(mtef5(), cb_header=27), "native_header_invalid"),
        (native_stream(mtef5(), version=0x00010000), "native_version_unsupported"),
        (native_stream(mtef5(), declared_size=100), "native_length_mismatch"),
        (native_stream(mtef5())[:-1], "native_length_mismatch"),
    ],
)
def test_native_wrapper_header_and_declared_length_are_strict(native_bytes, expected_reason):
    result = extract_native_equation(compound_with_native(native_bytes))
    assert result["status"] == "unavailable"
    assert result["reason_code"] == expected_reason


def test_invalid_cfb_and_missing_native_stream_are_unavailable():
    invalid = extract_native_equation(b"not a compound file")
    missing = extract_native_equation(
        compound_with_native(native_stream(mtef5()), stream_name="Other Stream")
    )

    assert invalid["status"] == "unavailable"
    assert invalid["reason_code"] == "invalid_cfb"
    assert missing["status"] == "unavailable"
    assert missing["reason_code"] == "equation_native_missing"
