from __future__ import annotations

import pytest
from app.latex_contracts import FormulaCatalog, FormulaConversion
from app.math_latex import (
    FormulaSourceError,
    _convert_mathml,
    _validate_template_variation,
    convert_ole_formula,
    convert_omml_formula,
    validate_formula_catalog,
)
from lxml import etree

MATHML = "http://www.w3.org/1998/Math/MathML"
OMML = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _math(source: str):
    return etree.fromstring(source.encode())


def test_mathml_fraction_radical_scripts_and_symbol_commands_convert() -> None:
    root = _math(
        f'<math xmlns="{MATHML}" display="block"><mrow>'
        "<mfrac><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow><mn>2</mn></mfrac>"
        "<mo>×</mo><msqrt><mn>9</mn></msqrt><msubsup><mi>a</mi><mn>1</mn><mn>2</mn></msubsup>"
        "</mrow></math>"
    )

    latex, serialized = _convert_mathml(root)

    assert r"\frac" in latex and r"\sqrt" in latex and r"\times" in latex
    assert "a" in latex and 'display="block"' in serialized


def test_degree_symbol_is_a_single_superscript_not_a_baseline_operator() -> None:
    root = _math(f'<math xmlns="{MATHML}"><mrow><mn>30</mn><mo>°</mo></mrow></math>')

    latex, _ = _convert_mathml(root)

    assert latex == r"30^{\circ}"


def test_pair_fences_become_scalable_latex_delimiters() -> None:
    root = _math(
        f'<math xmlns="{MATHML}"><mrow><mo>|</mo><mrow><mi>x</mi><mo>−</mo><mn>1</mn></mrow><mo>|</mo></mrow></math>'
    )

    latex, _ = _convert_mathml(root)

    assert r"\left\vert" in latex and r"\right\vert" in latex


def test_unknown_mathml_element_attribute_and_private_use_fail_closed() -> None:
    for source, expected in (
        (
            f'<math xmlns="{MATHML}"><mystery><mi>x</mi></mystery></math>',
            "mathml_element_unsupported",
        ),
        (f'<math xmlns="{MATHML}" onclick="bad"><mi>x</mi></math>', "mathml_attribute_unsupported"),
        (f'<math xmlns="{MATHML}"><mi>\uec07</mi></math>', "unmapped_private_use_character"),
    ):
        try:
            _convert_mathml(_math(source))
        except FormulaSourceError as exc:
            assert exc.code == expected
        else:
            raise AssertionError(f"expected explicit failure: {expected}")


def test_unknown_template_variation_bits_are_not_ignored() -> None:
    record = {"selector": "tmFRACT", "variation_code": 0x40}

    try:
        _validate_template_variation(record)
    except FormulaSourceError as exc:
        assert exc.code == "unknown_mtef_template_variation"
    else:
        raise AssertionError("unknown fraction variation was accepted")


def test_unknown_or_non_cfb_ole_is_explicit_unsupported() -> None:
    result = convert_ole_formula(b"not a compound file")

    assert isinstance(result, FormulaConversion)
    assert result.status == "unsupported"
    assert result.latex is None
    assert result.errors == ["invalid_cfb"]


def _omml(fragment: str) -> str:
    return f'<m:oMath xmlns:m="{OMML}">{fragment}</m:oMath>'


def test_omml_fraction_root_and_scripts_convert_to_latex() -> None:
    source = _omml(
        "<m:f><m:num><m:r><m:t>x+1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>"
        "<m:sSup><m:e><m:r><m:t>a</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>"
    )

    result = convert_omml_formula(source)

    assert result.status == "converted"
    assert r"\frac{x+1}{2}" in result.latex
    assert "^" in result.latex


def test_omml_hidden_radical_empty_degree_is_normalized_safely() -> None:
    source = _omml(
        '<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/>'
        "<m:e><m:r><m:t>9</m:t></m:r></m:e></m:rad>"
    )

    result = convert_omml_formula(source)

    assert result.status == "converted"
    assert r"\sqrt{9}" in result.latex


def test_omml_hidden_nonempty_degree_and_doctype_are_unsupported() -> None:
    hidden = _omml(
        '<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr>'
        "<m:deg><m:r><m:t>3</m:t></m:r></m:deg><m:e><m:r><m:t>9</m:t></m:r></m:e></m:rad>"
    )
    malicious = f'<!DOCTYPE x [<!ENTITY a "x">]><m:oMath xmlns:m="{OMML}"><m:r><m:t>&a;</m:t></m:r></m:oMath>'

    hidden_result = convert_omml_formula(hidden)
    malicious_result = convert_omml_formula(malicious)

    assert hidden_result.status == "unsupported"
    assert hidden_result.errors == ["omml_hidden_degree_not_empty"]
    assert malicious_result.status == "unsupported"
    assert malicious_result.errors == ["omml_doctype_forbidden"]


def test_catalog_validation_rebuilds_instead_of_trusting_source_hash(monkeypatch, tmp_path) -> None:
    digest = "a" * 64
    expected = FormulaCatalog(
        snapshot_id="snapshot",
        bank_sha256="bank",
        entries={
            digest: FormulaConversion(
                source_kind="ole",
                source_sha256=digest,
                native_payload_sha256="b" * 64,
                status="converted",
                latex="x+1",
                mathml=f'<math xmlns="{MATHML}"><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></math>',
                converter="synthetic-test",
                errors=[],
            )
        },
    )
    tampered = expected.model_copy(deep=True)
    tampered.entries[digest].latex = "x+2"
    monkeypatch.setattr("app.math_latex.build_formula_catalog", lambda _snapshot, _bank: expected)

    with pytest.raises(ValueError, match="differs from frozen source conversion"):
        validate_formula_catalog(object(), tampered, tmp_path)
