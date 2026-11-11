"""Independent synthetic LaTeX safety and source mathematics acceptance."""

import pytest
from app.latex_contracts import FormulaCatalog, FormulaConversion, validate_math_latex


@pytest.mark.parametrize(
    "payload",
    [
        r"\input{outside.tex}",
        r"\write18{touch unsafe}",
        r"\includegraphics{outside.png}",
        r"\newcommand{\safe}{1}",
        r"\^^69nput{outside.tex}",
        r"^^5cinput{outside.tex}",
        "\ue000",
        "\U000f0000",
        "\U00100000",
        "   \t\n",
    ],
)
def test_unsafe_or_unmapped_math_is_rejected_without_executing_tex(payload):
    with pytest.raises(ValueError):
        validate_math_latex(payload)


def test_safe_nested_math_keeps_radical_and_exponent_scope():
    value = r"\sqrt{(\frac{1}{3})^{-2}+1}-\sqrt{(-3)^2}+51^{\circ}"
    assert validate_math_latex(value) == value


def test_unsupported_formula_cannot_also_claim_latex_or_silent_success():
    base = {"source_kind": "ole", "source_sha256": "a" * 64, "converter": "synthetic"}
    for update in [
        {"status": "converted", "latex": "1", "errors": ["unknown_record"]},
        {"status": "unsupported", "latex": "1", "errors": ["unknown_record"]},
        {"status": "unsupported", "errors": []},
    ]:
        with pytest.raises(ValueError):
            FormulaConversion.model_validate({**base, **update})


def test_catalog_key_must_bind_complete_source_hash():
    item = FormulaConversion(
        source_kind="ole",
        source_sha256="a" * 64,
        status="converted",
        latex="1",
        mathml='<math xmlns="http://www.w3.org/1998/Math/MathML"><mn>1</mn></math>',
        converter="synthetic",
    )
    with pytest.raises(ValueError):
        FormulaCatalog(snapshot_id="synthetic", bank_sha256="b" * 64, entries={"c" * 64: item})


def synthetic_ole(records):
    """A complete small CFB containing only synthetic MTEF5, with correct miniFAT chains."""
    import math
    import struct

    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    payload = bytes([5, 1, 0, 7, 8]) + b"DSMT7\0\0" + records
    stream = struct.pack("<HIHI", 28, 0x00020000, 0xC34B, len(payload)) + bytes(16) + payload
    assert len(stream) <= 512

    def directory(name, kind, *, child=free, start=end, size=0):
        data = bytearray(128)
        encoded = name.encode("utf-16le") + b"\0\0"
        data[: len(encoded)] = encoded
        struct.pack_into("<HBBIII", data, 64, len(encoded), kind, 1, free, free, child)
        struct.pack_into("<IQ", data, 116, start, size)
        return data

    entries = directory("Root Entry", 5, child=1, start=2, size=512)
    entries += directory("Equation Native", 2, start=0, size=len(stream))
    entries += bytes(512 - len(entries))
    mini = [free] * 128
    count = math.ceil(len(stream) / 64)
    for index in range(count):
        mini[index] = index + 1 if index + 1 < count else end
    fat = [end, end, end, fat_sector] + [free] * 124
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<III", header, 40, 0, 1, 0)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<IIII", header, 60, 1, 1, end, 0)
    struct.pack_into("<109I", header, 76, 3, *([free] * 108))
    return (
        bytes(header)
        + bytes(entries)
        + struct.pack("<128I", *mini)
        + stream
        + bytes(512 - len(stream))
        + struct.pack("<128I", *fat)
    )


def mtef_char(value, *, typeface=8):
    import struct

    return bytes([2, 0, 128 + typeface]) + struct.pack("<H", ord(value))


def mtef_line(*parts):
    return bytes([1, 0]) + b"".join(parts) + b"\0"


def converted(records):
    import hashlib

    from app.math_latex import convert_ole_formula

    source = synthetic_ole(records)
    result = convert_ole_formula(source)
    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert result.status == "converted", result.errors
    assert result.latex and result.mathml
    return result


def mathml_text(value):
    from lxml import etree

    return "".join(etree.fromstring(value.encode()).itertext())


def latex_argument(text, position):
    """Extract an argument independently; nested braces cannot change the asserted scope."""
    while position < len(text) and text[position].isspace():
        position += 1
    assert position < len(text)
    if text[position] != "{":
        return text[position], position + 1
    start, depth = position + 1, 1
    position += 1
    while position < len(text):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
        if depth == 0:
            return text[start:position], position + 1
        position += 1
    raise AssertionError("unclosed LaTeX argument")


def test_native_digits_decimal_and_minus_are_not_replaced_or_dropped():
    import re

    record = mtef_line(*(mtef_char(c, typeface=6 if c == "−" else 8) for c in "−0.5")) + b"\0"
    result = converted(record)
    assert mathml_text(result.mathml).replace("−", "-") == "-0.5"
    assert "0.5" in re.sub(r"\s+", "", result.latex)


def test_native_fraction_keeps_entire_denominator():
    import re

    fraction = (
        bytes([3, 0, 11, 0, 0])
        + mtef_line(mtef_char("1"))
        + mtef_line(*(mtef_char(c) for c in "2+3"))
        + b"\0"
    )
    result = converted(mtef_line(fraction) + b"\0")
    match = re.search(r"\\(?:dfrac|tfrac|frac)", result.latex)
    assert match
    numerator, end = latex_argument(result.latex, match.end())
    denominator, _ = latex_argument(result.latex, end)

    def clean(value):
        return re.sub(r"[{}\s]", "", value)

    assert clean(numerator) == "1" and clean(denominator) == "2+3"


@pytest.mark.parametrize(
    "case", ["unknown_record", "trailing_expression", "unknown_fraction_variant", "unknown_pua"]
)
def test_native_unknown_or_unconsumed_semantics_fail_closed(case):
    from app.math_latex import convert_ole_formula

    one = mtef_char("1")
    if case == "unknown_record":
        records = mtef_line(bytes([50, 1, 255]), one) + b"\0"
    elif case == "trailing_expression":
        records = mtef_line(one) + mtef_line(mtef_char("2")) + b"\0"
    elif case == "unknown_fraction_variant":
        fraction = bytes([3, 0, 11, 64, 0]) + mtef_line(one) + mtef_line(mtef_char("2")) + b"\0"
        records = mtef_line(fraction) + b"\0"
    else:
        records = mtef_line(mtef_char("\ue123", typeface=6)) + b"\0"
    result = convert_ole_formula(synthetic_ole(records))
    assert result.status == "unsupported" and result.errors
    assert result.latex is None
    expected = {
        "unknown_record": {"unknown_mtef_record"},
        "trailing_expression": {"mtef_trailing_records"},
        "unknown_pua": {"unmapped_mtef_character", "unmapped_private_use_character"},
    }
    if case == "unknown_fraction_variant":
        assert any("variation" in error or "variant" in error for error in result.errors)
    else:
        assert set(result.errors) & expected[case]


def test_symbol_font_position_without_mtcode_is_either_correctly_mapped_or_rejected():
    from app.math_latex import convert_ole_formula

    # MTEF CHAR opts=ENC8|NO_MT; Symbol position D0 is an angle, not ASCII D0.
    result = convert_ole_formula(synthetic_ole(mtef_line(bytes([2, 0x24, 0x86, 0xD0])) + b"\0"))
    if result.status == "converted":
        assert "∠" in mathml_text(result.mathml)
        assert r"\angle" in result.latex or "∠" in result.latex
    else:
        assert result.errors and result.latex is None


def test_omml_radical_scope_does_not_absorb_the_following_addend():
    import re

    from app.math_latex import convert_omml_formula

    xml = '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e><m:r><m:t>9</m:t></m:r></m:e></m:rad><m:r><m:t>+1</m:t></m:r></m:oMath>'
    result = convert_omml_formula(xml)
    assert result.status == "converted", result.errors
    match = re.search(r"\\sqrt", result.latex)
    assert match
    argument, end = latex_argument(result.latex, match.end())
    assert re.sub(r"[{}\s]", "", argument) == "9"
    assert "+1" in re.sub(r"[{}\s]", "", result.latex[end:])


def synthetic_source_v3_fixture(tmp_path, *, image=False):
    import hashlib
    import json

    from app.bank_practice import build_bank_practice
    from test_source_exporting import _make_fixture

    bank_dir, bank, curriculum, progress, snapshot, _, _ = _make_fixture(tmp_path, image=image)
    raw = json.loads((bank_dir / "bank.json").read_bytes())
    raw["schema_version"] = "source-bank-v3"
    content = json.dumps(raw, ensure_ascii=False).encode()
    (bank_dir / "bank.json").write_bytes(content)
    bank.source_bank_sha256 = hashlib.sha256(content).hexdigest()
    snapshot = build_bank_practice(
        bank, curriculum, progress, snapshot.request, is_synthetic_student=True
    )
    return bank_dir, bank, curriculum, progress, snapshot


@pytest.mark.parametrize("drift", ["catalog_math", "source_docx", "geometry_asset"])
def test_resigned_catalog_or_source_drift_is_rejected_before_compilation(
    tmp_path, monkeypatch, drift
):
    from app.exporting import ExportError
    from app.latex_exporting import export_latex_snapshot
    from app.math_latex import build_formula_catalog

    bank_dir, bank, curriculum, progress, snapshot = synthetic_source_v3_fixture(
        tmp_path, image=drift == "geometry_asset"
    )
    catalog = build_formula_catalog(snapshot, bank_dir)
    assert all(entry.status == "converted" for entry in catalog.entries.values())
    if drift == "catalog_math":
        # All claimed source hashes and snapshot identities remain valid; only math is changed.
        entry = next(iter(catalog.entries.values()))
        entry.latex = "999"
    elif drift == "source_docx":
        source = bank_dir / "sources/source.docx"
        source.write_bytes(source.read_bytes() + b"synthetic-source-drift")
    else:
        source = bank_dir / "media/doc1/picture.png"
        source.write_bytes(source.read_bytes() + b"synthetic-asset-drift")

    def forbidden_compile(*args, **kwargs):
        raise AssertionError("source validation must precede compilation")

    monkeypatch.setattr("app.latex_exporting._compile_pdf", forbidden_compile)
    output = tmp_path / "rejected"
    with pytest.raises(ExportError):
        export_latex_snapshot(
            snapshot,
            output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=catalog,
            xelatex_executable=__import__("sys").executable,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".rejected-*"))


def test_second_pdf_compile_failure_discards_first_pdf_and_stage(tmp_path, monkeypatch):
    from app.exporting import ExportError
    from app.latex_exporting import export_latex_snapshot
    from app.math_latex import build_formula_catalog

    bank_dir, bank, curriculum, progress, snapshot = synthetic_source_v3_fixture(tmp_path)
    catalog = build_formula_catalog(snapshot, bank_dir)
    compiled = []

    def synthetic_compile(tex_path, build_dir, executable):
        compiled.append(tex_path.name)
        if len(compiled) == 2:
            raise ExportError("synthetic teacher compile failure")
        build_dir.mkdir()
        pdf = build_dir / "student.pdf"
        pdf.write_bytes(b"%PDF-1.4\nexplicitly synthetic transaction marker")
        return pdf

    monkeypatch.setattr("app.latex_exporting._compile_pdf", synthetic_compile)
    output = tmp_path / "atomic"
    with pytest.raises(ExportError, match="synthetic teacher"):
        export_latex_snapshot(
            snapshot,
            output,
            bank_dir=bank_dir,
            practice_bank=bank,
            curriculum=curriculum,
            current_progress=progress,
            formula_catalog=catalog,
            xelatex_executable=__import__("sys").executable,
        )
    assert compiled == ["student.tex", "teacher.tex"]
    assert not output.exists()
    assert not list(tmp_path.glob(".atomic-*"))


def test_source_text_tex_commands_are_printed_as_literal_text():
    from app.latex_exporting import _tex_text

    source = r"\input{private} 50% & x_1 # $ ^^5cwrite18"
    result = _tex_text(source)
    assert result == (
        r"\textbackslash{}input\{private\} 50\% \& x\_1 \# \$ "
        r"\textasciicircum{}\textasciicircum{}5cwrite18"
    )
