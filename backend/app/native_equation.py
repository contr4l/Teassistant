"""Read-only extraction of exact MathType Equation Native MTEF payloads.

This module establishes byte identity for source equation objects. It does not
decode MTEF records, normalize presentation, or claim mathematical equivalence.
"""

from __future__ import annotations

import hashlib
import io
import struct
from typing import Any

import olefile

METHOD = "native_mtef_exact_v1"
_NATIVE_STREAM = ("Equation Native",)
_NATIVE_HEADER_SIZE = 28
_NATIVE_VERSION = 0x00020000
_MTEF_VERSION = 5
_MTEF_HEADER_FIXED_SIZE = 5
_MTEF_APP_KEY_MAX = 64


def _unavailable(ole_sha256: str, reason_code: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "method": METHOD,
        "payload_sha256": None,
        "payload_size": None,
        "mtef_version": None,
        "ole_sha256": ole_sha256,
        "reason_code": reason_code,
    }


def _read_native_stream(ole_bytes: bytes) -> tuple[bytes | None, str | None]:
    compound: olefile.OleFileIO | None = None
    try:
        compound = olefile.OleFileIO(
            io.BytesIO(ole_bytes),
            write_mode=False,
            raise_defects=olefile.DEFECT_INCORRECT,
        )
        if compound.parsing_issues:
            return None, "invalid_cfb"
        paths = compound.listdir(streams=True, storages=False)
        named_matches = [tuple(path) for path in paths if path and path[-1] == "Equation Native"]
        matches = [path for path in named_matches if path == _NATIVE_STREAM]
        if not matches:
            return None, "equation_native_ambiguous" if named_matches else "equation_native_missing"
        if len(matches) != 1 or len(named_matches) != 1:
            return None, "equation_native_ambiguous"
        return compound.openstream(matches[0]).read(), None
    except Exception:
        return None, "invalid_cfb"
    finally:
        if compound is not None:
            compound.close()


def _validate_native_stream(data: bytes) -> str | None:
    if len(data) < _NATIVE_HEADER_SIZE:
        return "native_header_truncated"
    try:
        header_size, version, clipboard_format, object_size = struct.unpack_from("<HIHI", data, 0)
    except struct.error:
        return "native_header_truncated"
    if header_size != _NATIVE_HEADER_SIZE:
        return "native_header_invalid"
    if version != _NATIVE_VERSION:
        return "native_version_unsupported"
    if clipboard_format == 0:
        return "native_clipboard_format_invalid"
    if object_size != len(data) - _NATIVE_HEADER_SIZE:
        return "native_length_mismatch"

    payload = data[_NATIVE_HEADER_SIZE:]
    if len(payload) < _MTEF_HEADER_FIXED_SIZE + 1:
        return "mtef_header_truncated"
    mtef_version, platform, product, product_version, _product_subversion = payload[:5]
    if mtef_version != _MTEF_VERSION:
        return "mtef_version_unsupported"
    if platform != 1 or product != 0 or product_version not in {5, 6, 7}:
        return "mtef_header_invalid"

    key_end_limit = min(len(payload), _MTEF_HEADER_FIXED_SIZE + _MTEF_APP_KEY_MAX + 1)
    key_end = payload.find(b"\0", _MTEF_HEADER_FIXED_SIZE, key_end_limit)
    if key_end < 0:
        return "mtef_app_key_invalid"
    try:
        app_key = payload[_MTEF_HEADER_FIXED_SIZE:key_end].decode("ascii")
    except UnicodeDecodeError:
        return "mtef_app_key_invalid"
    if app_key != f"DSMT{product_version}":
        return "mtef_app_key_invalid"
    options_index = key_end + 1
    if options_index >= len(payload):
        return "mtef_header_truncated"
    if payload[options_index] & 0xFE:
        return "mtef_options_invalid"
    if options_index + 1 >= len(payload):
        return "mtef_records_missing"
    # END is required at the end of the declared MTEF payload. This is a
    # framing check; this module does not parse or certify every record.
    if payload[-1] != 0:
        return "mtef_end_missing"
    return None


def extract_native_equation(ole_bytes: bytes) -> dict[str, Any]:
    """Return a SHA-256 identity for a strictly framed Equation Native stream.

    ``payload_sha256`` covers every byte after the 28-byte OLE equation wrapper,
    including the MTEF v5 header, known records, and opaque future records.
    Nothing is skipped or normalized. Invalid or unsupported containers return
    an explicit ``unavailable`` result and never produce a payload hash.
    """
    if not isinstance(ole_bytes, bytes):
        raise TypeError("ole_bytes must be bytes")
    ole_digest = hashlib.sha256(ole_bytes).hexdigest()
    data, reason = _read_native_stream(ole_bytes)
    if reason is not None or data is None:
        return _unavailable(ole_digest, reason or "equation_native_missing")
    reason = _validate_native_stream(data)
    if reason is not None:
        return _unavailable(ole_digest, reason)

    payload = data[_NATIVE_HEADER_SIZE:]
    return {
        "status": "valid",
        "method": METHOD,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_size": len(payload),
        "mtef_version": payload[0],
        "ole_sha256": ole_digest,
        "reason_code": None,
    }
