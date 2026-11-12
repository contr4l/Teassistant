"""Independent, purely synthetic MathType enhancement counterexamples."""

import re

import pytest
from app.math_latex import convert_ole_formula
from lxml import etree
from test_latex_acceptance import mtef_char, mtef_line, synthetic_ole


def _bar(
    body,
    *,
    before=b"",
    between=b"",
    left="\uec07",
    right="\uec08",
    after=b"",
    variant=3,
    marker_typeface=22,
):
    # MTEF5 TMPL: selector=tmBAR, paired-fence variation, then explicit slots.
    return (
        bytes([3, 0, 4, variant, 0])
        + before
        + mtef_line(body)
        + between
        + mtef_char(left, typeface=marker_typeface)
        + (mtef_char(right, typeface=marker_typeface) if right is not None else b"")
        + after
        + b"\0"
    )


def _conversion(template):
    return convert_ole_formula(synthetic_ole(mtef_line(template) + b"\0"))


def _content(result):
    assert result.status == "converted", result.errors
    assert result.mathml and result.latex and result.errors == []
    return "".join(etree.fromstring(result.mathml.encode()).itertext())


@pytest.mark.parametrize(
    "before,between", [(b"", b""), (b"\x0f\0", b""), (b"", b"\x0f\0"), (b"\x0f\0", b"\x0a")]
)
def test_documented_fence_slots_survive_color_and_size_controls(before, between):
    result = _conversion(_bar(mtef_char("x", typeface=3), before=before, between=between))
    assert _content(result).replace(" ", "") == "|x|"
    assert "\\left\\vert" in result.latex and "\\right\\vert" in result.latex
    assert "\uec07" not in result.latex and "\uec08" not in result.latex


def test_fence_normalization_preserves_decimal_negative_and_relation_operators():
    body = b"".join(mtef_char(c, typeface=6 if c in "−≤" else 8) for c in "−0.25≤1")
    result = _conversion(_bar(body, before=b"\x0f\0"))
    assert _content(result).replace(" ", "") == "|−0.25≤1|"
    latex = re.sub(r"\s+", "", result.latex)
    assert "0.25" in latex and ("\\leq" in latex or "\\le" in latex)
    assert "-" in latex


@pytest.mark.parametrize(
    "left,right", [("\uec08", "\uec07"), ("\uec07", "\uec09"), ("\uec06", "\uec08")]
)
def test_nearby_or_reversed_private_glyphs_are_not_silently_discarded(left, right):
    result = _conversion(_bar(mtef_char("2"), left=left, right=right))
    assert result.status == "unsupported"
    assert result.latex is None and result.mathml is None and result.errors


def test_marker_pair_outside_fence_is_not_erased():
    result = convert_ole_formula(
        synthetic_ole(
            mtef_line(
                mtef_char("2"), mtef_char("\uec07", typeface=22), mtef_char("\uec08", typeface=22)
            )
            + b"\0"
        )
    )
    assert result.status == "unsupported"
    assert result.latex is None and result.errors


@pytest.mark.parametrize("position", ["between", "after"])
def test_extra_mathematical_slot_cannot_be_lost_in_fence_cleanup(position):
    kwargs = {position: mtef_char("7")}
    result = _conversion(_bar(mtef_char("2"), **kwargs))
    assert result.status == "unsupported"
    assert result.latex is None and result.errors


def test_unknown_record_in_nearby_control_position_is_still_rejected():
    result = _conversion(_bar(mtef_char("2"), before=b"\x63"))
    assert result.status == "unsupported"
    assert result.errors == ["unknown_mtef_record"]


def test_unknown_fence_variation_does_not_gain_support_from_glyph_pair():
    result = _conversion(_bar(mtef_char("2"), variant=0x43))
    assert result.status == "unsupported"
    assert result.errors == ["unknown_mtef_template_variation"]


@pytest.mark.parametrize("kwargs", [{"right": None}, {"marker_typeface": 8}])
def test_incomplete_or_wrong_typeface_marker_binding_is_rejected(kwargs):
    result = _conversion(_bar(mtef_char("2"), **kwargs))
    assert result.status == "unsupported"
    assert result.latex is None and result.errors


@pytest.mark.parametrize("control", [b"\x0f\x09", b"\x09\x65\x00\x00"])
def test_undefined_color_and_zero_absolute_size_cannot_hide_as_control_slots(control):
    result = _conversion(_bar(mtef_char("2"), before=control))
    assert result.status == "unsupported"
    assert result.latex is None and result.errors


def _pi_record(*, mt_code="π", font_position=112, embellishment=b""):
    import struct

    options = 4 | (32 if mt_code is None else 0) | (1 if embellishment else 0)
    return (
        bytes([2, options, 128 + 4])
        + (struct.pack("<H", ord(mt_code)) if mt_code is not None else b"")
        + bytes([font_position])
        + embellishment
    )


def test_pi_identity_comes_from_explicit_mtcode():
    result = _conversion(_bar(_pi_record(), before=b"\x0f\0"))
    assert _content(result) == "|π|"
    assert "\\pi" in result.latex


@pytest.mark.parametrize("code", [None, "\ue123"])
def test_pi_font_position_without_matching_mtcode_is_not_guessed(code):
    result = convert_ole_formula(synthetic_ole(mtef_line(_pi_record(mt_code=code)) + b"\0"))
    assert result.status == "unsupported"
    assert result.latex is None and result.errors


def test_pi_embellishment_is_preserved_or_explicitly_unsupported():
    # MTEF EMBELL code 2 = one dot. Losing it changes the expression.
    record = _pi_record(embellishment=b"\x06\0\x02\0")
    result = convert_ole_formula(synthetic_ole(mtef_line(record) + b"\0"))
    if result.status == "converted":
        assert result.latex and "\\pi" in result.latex and "\\dot" in result.latex
    else:
        assert result.latex is None and result.errors


@pytest.mark.parametrize("side", [0, 1])
def test_fence_glyph_mathematical_embellishment_cannot_be_deleted(side):
    glyphs = [mtef_char("\uec07", typeface=22), mtef_char("\uec08", typeface=22)]
    embellished = bytearray(glyphs[side])
    embellished[1] |= 1
    glyphs[side] = bytes(embellished) + b"\x06\0\x02\0"
    template = bytes([3, 0, 4, 3, 0]) + mtef_line(mtef_char("2")) + b"".join(glyphs) + b"\0"
    result = _conversion(template)
    assert result.status == "unsupported"
    assert result.latex is None and result.errors
