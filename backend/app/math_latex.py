"""Strict, source-bound conversion of embedded formulas to LaTeX.

MathType conversion uses the pinned mathtypejx parser only after checking the
native MTEF record path and parser consumption. It never uses the PNG preview.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import olefile
from lxml import etree

from app.bank_practice_contracts import BankPracticeSnapshot
from app.latex_contracts import FormulaCatalog, FormulaConversion, validate_math_latex
from app.native_equation import extract_native_equation
from app.semantic_bank import canonical_hash

_CONVERTER = "mathtypejx@7f1b46358a3a7b0b5c992faa0dca91740a54efbb+strict-v2 (MIT)"
_PARSER_LOCK = threading.RLock()
_NS = "http://www.w3.org/1998/Math/MathML"
_MATHML_TAGS = frozenset(
    {
        "math",
        "mrow",
        "mi",
        "mn",
        "mo",
        "mtext",
        "mfrac",
        "msqrt",
        "mroot",
        "msub",
        "msup",
        "msubsup",
        "munder",
        "mover",
        "munderover",
        "mfenced",
        "mtable",
        "mtr",
        "mtd",
    }
)
_MATHML_ATTRS = frozenset(
    {
        "display",
        "mathvariant",
        "stretchy",
        "fence",
        "accent",
        "accentunder",
        "open",
        "close",
        "separators",
    }
)
_MATHVARIANTS = frozenset(
    {"normal", "italic", "bold", "bold-italic", "double-struck", "script", "fraktur"}
)
_UNICODE_TEX = {
    "×": r"\times",
    "÷": r"\div",
    "−": "-",
    "°": r"^{\circ}",
    "∠": r"\angle",
    "∥": r"\parallel",
    "∴": r"\therefore",
    "∵": r"\because",
    "⊙": r"\odot",
    "⊥": r"\perp",
    "△": r"\triangle",
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\neq",
    "≈": r"\approx",
    "∞": r"\infty",
    "π": r"\pi",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "θ": r"\theta",
    "∈": r"\in",
    "∉": r"\notin",
    "∪": r"\cup",
    "∩": r"\cap",
    "·": r"\cdot",
    "|": r"\vert",
    "‖": r"\Vert",
    "⌈": r"\lceil",
    "⌉": r"\rceil",
    "⌊": r"\lfloor",
    "⌋": r"\rfloor",
}
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "%": r"\%",
    "&": r"\&",
    "#": r"\#",
    "$": r"\$",
}
_MTEF_KNOWN_RECORDS: frozenset[int] | None = None


class FormulaSourceError(ValueError):
    """Safe conversion error code without source content or response data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unsupported(
    kind: str, source_sha: str, code: str, *, payload_sha: str | None = None
) -> FormulaConversion:
    return FormulaConversion(
        source_kind=kind,
        source_sha256=source_sha,
        native_payload_sha256=payload_sha,
        status="unsupported",
        latex=None,
        mathml=None,
        converter=_CONVERTER if kind == "ole" else "source_math+strict-mathml-v1",
        errors=[code],
    )


def _mathml_text(element: etree._Element) -> str:
    return "".join(element.itertext())


def _escape_text(value: str) -> str:
    if not value or any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        raise FormulaSourceError("empty_or_control_character")
    if any(
        "\ue000" <= char <= "\uf8ff"
        or 0xF0000 <= ord(char) <= 0xFFFFD
        or 0x100000 <= ord(char) <= 0x10FFFD
        for char in value
    ):
        raise FormulaSourceError("unmapped_private_use_character")
    if len(value) > 10000:
        raise FormulaSourceError("math_text_too_long")
    return "".join(_TEX_ESCAPES.get(char, char) for char in value)


def _math_token(value: str) -> str:
    if not value:
        raise FormulaSourceError("empty_math_token")
    if any(
        "\ue000" <= char <= "\uf8ff"
        or 0xF0000 <= ord(char) <= 0xFFFFD
        or 0x100000 <= ord(char) <= 0x10FFFD
        for char in value
    ):
        raise FormulaSourceError("unmapped_private_use_character")
    out: list[str] = []
    for char in value:
        if char in _UNICODE_TEX:
            replacement = _UNICODE_TEX[char]
            out.append(replacement if replacement.endswith("}") else replacement + " ")
        elif char.isascii() and (char.isalpha() or char.isdigit() or char in "()+-=.,:;!?[]<>/"):
            out.append(char)
        elif char in "  ":
            out.append(" ")
        else:
            raise FormulaSourceError("unmapped_math_character")
    return "".join(out)


def _children(element: etree._Element) -> list[etree._Element]:
    if element.text and element.text.strip():
        raise FormulaSourceError("unexpected_mathml_text_node")
    for child in element:
        if child.tail and child.tail.strip():
            raise FormulaSourceError("unexpected_mathml_tail")
    return list(element)


def _validate_mathml_tree(root: etree._Element) -> None:
    if etree.QName(root).localname != "math":
        raise FormulaSourceError("mathml_root_invalid")
    for node in root.iter():
        if not isinstance(node.tag, str):
            raise FormulaSourceError("mathml_node_invalid")
        qname = etree.QName(node)
        if qname.namespace not in {None, _NS} or qname.localname not in _MATHML_TAGS:
            raise FormulaSourceError("mathml_element_unsupported")
        for name, value in node.attrib.items():
            local = etree.QName(name).localname
            if local == "xmlns" or name.startswith("{http://www.w3.org/2000/xmlns/}"):
                continue
            if local not in _MATHML_ATTRS or len(value) > 64:
                raise FormulaSourceError("mathml_attribute_unsupported")
            if local == "display" and (
                qname.localname != "math" or value not in {"inline", "block"}
            ):
                raise FormulaSourceError("mathml_attribute_unsupported")
            if local == "mathvariant" and value not in _MATHVARIANTS:
                raise FormulaSourceError("mathvariant_unsupported")


def _wrap_variant(value: str, variant: str | None) -> str:
    if variant in {None, "italic"}:
        return value
    if variant == "normal":
        return rf"\mathrm{{{value}}}"
    if variant == "bold":
        return rf"\mathbf{{{value}}}"
    if variant == "bold-italic":
        return rf"\boldsymbol{{{value}}}"
    if variant == "double-struck":
        return rf"\mathbb{{{value}}}"
    if variant == "script":
        return rf"\mathcal{{{value}}}"
    if variant == "fraktur":
        return rf"\mathfrak{{{value}}}"
    raise FormulaSourceError("mathvariant_unsupported")


def _serialize_mathml(element: etree._Element) -> str:
    tag = etree.QName(element).localname
    if tag in {"mi", "mn", "mo"}:
        if len(element):
            raise FormulaSourceError("mathml_token_has_children")
        value = _math_token(_mathml_text(element))
        return _wrap_variant(value, element.get("mathvariant"))
    if tag == "mtext":
        if len(element):
            raise FormulaSourceError("mathml_token_has_children")
        return rf"\text{{{_escape_text(_mathml_text(element))}}}"
    children = _children(element)
    if tag in {"math", "mrow"}:
        if tag == "math" and len(children) != 1:
            raise FormulaSourceError("mathml_top_level_arity")
        if tag == "mrow" and len(children) >= 2:
            left_node, right_node = children[0], children[-1]
            if (
                etree.QName(left_node).localname == "mo"
                and etree.QName(right_node).localname == "mo"
            ):
                left_text = _mathml_text(left_node)
                right_text = _mathml_text(right_node)
                if (left_text, right_text) in {
                    ("(", ")"),
                    ("[", "]"),
                    ("{", "}"),
                    ("|", "|"),
                    ("‖", "‖"),
                    ("⌈", "⌉"),
                    ("⌊", "⌋"),
                    ("<", ">"),
                }:
                    left = _math_token(left_text).strip()
                    right = _math_token(right_text).strip()
                    body = "".join(_serialize_mathml(child) for child in children[1:-1])
                    left_gap = " " if left.startswith("\\") else ""
                    right_gap = " " if right.startswith("\\") else ""
                    return rf"\left{left}{left_gap}{body}\right{right}{right_gap}"
        return "".join(_serialize_mathml(child) for child in children)
    rendered = [_serialize_mathml(child) for child in children]
    if tag == "mfrac" and len(rendered) == 2:
        return rf"\frac{{{rendered[0]}}}{{{rendered[1]}}}"
    if tag == "msqrt" and rendered:
        return rf"\sqrt{{{''.join(rendered)}}}"
    if tag == "mroot" and len(rendered) == 2:
        return rf"\sqrt[{rendered[1]}]{{{rendered[0]}}}"
    if tag in {"msub", "msup"} and len(rendered) == 2:
        operator = "_" if tag == "msub" else "^"
        return rf"{{{rendered[0]}}}{operator}{{{rendered[1]}}}"
    if tag == "msubsup" and len(rendered) == 3:
        return rf"{{{rendered[0]}}}_{{{rendered[1]}}}^{{{rendered[2]}}}"
    if tag == "mover" and len(rendered) == 2:
        if element.get("accent") == "true" and rendered[1] in {"^", r"\hat{}"}:
            return rf"\hat{{{rendered[0]}}}"
        return rf"\overset{{{rendered[1]}}}{{{rendered[0]}}}"
    if tag == "munder" and len(rendered) == 2:
        return rf"\underset{{{rendered[1]}}}{{{rendered[0]}}}"
    if tag == "munderover" and len(rendered) == 3:
        return rf"\overset{{{rendered[2]}}}{{\underset{{{rendered[1]}}}{{{rendered[0]}}}}}"
    if tag == "mfenced":
        opening = element.get("open", "(")
        closing = element.get("close", ")")
        if len(opening) > 2 or len(closing) > 2:
            raise FormulaSourceError("mathml_fence_unsupported")
        left = _math_token(opening) if opening else "."
        right = _math_token(closing) if closing else "."
        return rf"\left{left}{','.join(rendered)}\right{right}"
    if tag == "mtable":
        rows: list[str] = []
        for row in children:
            if etree.QName(row).localname != "mtr":
                raise FormulaSourceError("mathml_table_row_invalid")
            cells = _children(row)
            if any(etree.QName(cell).localname != "mtd" for cell in cells):
                raise FormulaSourceError("mathml_table_cell_invalid")
            rows.append(" & ".join(_serialize_mathml(cell) for cell in cells))
        return rf"\begin{{matrix}}{' \\\\ '.join(rows)}\end{{matrix}}"
    if tag == "mtr":
        return " & ".join(rendered)
    if tag == "mtd":
        return "".join(rendered)
    raise FormulaSourceError("mathml_structure_unsupported")


def _convert_mathml(root: etree._Element) -> tuple[str, str]:
    _validate_mathml_tree(root)
    mathml = etree.tostring(root, encoding="unicode")
    latex = _serialize_mathml(root)
    return validate_math_latex(latex), mathml


def _ole_native_stream(ole_bytes: bytes) -> bytes:
    try:
        with olefile.OleFileIO(
            io.BytesIO(ole_bytes), write_mode=False, raise_defects=olefile.DEFECT_INCORRECT
        ) as compound:
            if compound.parsing_issues:
                raise FormulaSourceError("invalid_cfb")
            streams = [tuple(path) for path in compound.listdir(streams=True, storages=False)]
            if streams.count(("Equation Native",)) != 1 or any(
                path[-1] == "Equation Native" and path != ("Equation Native",) for path in streams
            ):
                raise FormulaSourceError("equation_native_stream_invalid")
            return compound.openstream("Equation Native").read()
    except FormulaSourceError:
        raise
    except Exception as exc:
        raise FormulaSourceError("invalid_cfb") from exc


def _strict_parse_mtef(mtef: bytes) -> etree._Element:
    try:
        from mathtypejx.mtef import builder, chars, mover, records5
        from mathtypejx.mtef.mathml import _skip_stream_header, _xslt_transform
        from mathtypejx.mtef.stream import ByteStream
    except ImportError as exc:
        raise FormulaSourceError("converter_unavailable") from exc

    known = {
        getattr(records5, name)
        for name in (
            "END",
            "LINE",
            "CHAR",
            "TMPL",
            "PILE",
            "MATRIX",
            "EMBELL",
            "RULER",
            "FONT_STYLE_DEF",
            "SIZE",
            "FULL",
            "SUB",
            "SUB2",
            "SYM",
            "SUBSYM",
            "COLOR",
            "COLOR_DEF",
            "FONT_DEF",
            "ENCODING_DEF",
            "EQN_PREFS",
            "MT_COMMENT",
        )
        if hasattr(records5, name)
    }
    stream = ByteStream(mtef)
    _skip_stream_header(stream, 5)
    original_dispatch = records5._dispatch
    original_future = records5._parse_future

    def strict_dispatch(current_stream, record_type):
        if record_type not in known:
            raise FormulaSourceError("unknown_mtef_record")
        result = original_dispatch(current_stream, record_type)
        if result is None and record_type not in {getattr(records5, "MT_COMMENT", -999)}:
            raise FormulaSourceError("unconsumed_mtef_record")
        return result

    def strict_future(_stream):
        raise FormulaSourceError("unknown_future_mtef_record")

    with _PARSER_LOCK:
        records5._dispatch = strict_dispatch
        records5._parse_future = strict_future
        try:
            tree = records5.parse_equation(stream)
        except FormulaSourceError:
            raise
        except Exception as exc:
            raise FormulaSourceError("mtef_parse_failed") from exc
        finally:
            records5._dispatch = original_dispatch
            records5._parse_future = original_future

    if tree.get("equation") is None:
        raise FormulaSourceError("mtef_equation_missing")
    if any(byte != 0 for byte in stream._data[stream.position :]):
        raise FormulaSourceError("mtef_trailing_records")

    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    for record in walk(tree):
        selector = record.get("selector")
        if isinstance(selector, str) and selector.startswith("tmUNKNOWN_"):
            raise FormulaSourceError("unknown_mtef_template")
        if record.get("type") == "tmpl":
            _validate_template_variation(record)

    _remove_known_bar_tail_markers(tree, walk)

    xml = builder.build_mtef_xml(tree)
    mover.move(xml)
    # The pinned table lacks these explicit MTCode mappings. Font positions
    # are not used to infer identity; private-use and unknown codes remain.
    _replace_known_mtef_characters(xml)
    chars.replace(xml)
    if list(xml.iter("char")):
        raise FormulaSourceError("unmapped_mtef_character")
    try:
        result = _xslt_transform(xml)
        if not result:
            raise FormulaSourceError("mathml_transform_empty")
        root = etree.fromstring(
            result.encode("utf-8"), parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except FormulaSourceError:
        raise
    except Exception as exc:
        raise FormulaSourceError("mathml_transform_failed") from exc
    return root


def _replace_known_mtef_characters(xml: etree._Element) -> None:
    replacements = {
        "0x007C": ("mo", "|"),
        "0x00D7": ("mo", "×"),
        "0x03C0": ("mi", "π"),
    }
    for char_node in list(xml.iter("char")):
        spec = replacements.get(char_node.findtext("mt_code_value") or "")
        if spec is None:
            continue
        tag, value = spec
        replacement = etree.Element(tag)
        replacement.text = value
        parent = char_node.getparent()
        if parent is None:
            raise FormulaSourceError("mtef_character_orphaned")
        parent.replace(char_node, replacement)


def _validate_template_variation(record: dict[str, Any]) -> None:
    """Reject selector variation bits the upstream parser silently ignores."""
    selector = record.get("selector")
    code = record.get("variation_code")
    if not isinstance(code, int) or code < 0:
        raise FormulaSourceError("mtef_template_variation_invalid")
    masks = {
        "tmANGLE": 0x03,
        "tmPAREN": 0x03,
        "tmBRACE": 0x03,
        "tmBRACK": 0x03,
        "tmBAR": 0x03,
        "tmDBAR": 0x03,
        "tmFLOOR": 0x03,
        "tmCEILING": 0x03,
        "tmOBRACK": 0x03,
        "tmROOT": 0x01,
        "tmFRACT": 0x07,
        "tmUBAR": 0x01,
        "tmOBAR": 0x01,
        "tmARROW": 0x3F,
        "tmINTEG": 0x10F,
        "tmSUM": 0x70,
        "tmPROD": 0x70,
        "tmCOPROD": 0x70,
        "tmUNION": 0x70,
        "tmINTER": 0x70,
        "tmINTOP": 0x70,
        "tmSUMOP": 0x70,
        "tmLIM": 0x01,
        "tmHBRACE": 0x01,
        "tmHBRACK": 0x01,
        "tmLDIV": 0x01,
        "tmSUB": 0x01,
        "tmSUP": 0x01,
        "tmSUBSUP": 0x01,
        "tmDIRAC": 0x03,
        "tmVEC": 0x0F,
        "tmTILDE": 0x00,
        "tmHAT": 0x00,
        "tmARC": 0x00,
        "tmJSTATUS": 0x00,
        "tmSTRIKE": 0x07,
        "tmBOX": 0x1F,
    }
    if selector == "tmINTERVAL":
        if code not in {0, 2, 3}:
            raise FormulaSourceError("unknown_mtef_template_variation")
    elif selector not in masks or code & ~masks[selector]:
        raise FormulaSourceError("unknown_mtef_template_variation")


def _remove_known_bar_tail_markers(tree: dict[str, Any], walk) -> None:
    """Normalize only confirmed fence glyphs in tmBAR's trailing pair slots.

    MTEF carries COLOR and FULL records alongside template objects. The known
    double-bar form has one body LINE, optional COLOR, optional FULL size, and
    the ordered EC07/EC08 pair at the end. Other records or object slots stay
    unsupported and the final private-use check rejects their glyphs.
    """
    templates = [record for record in walk(tree) if record.get("type") == "tmpl"]
    color_definitions = sum(1 for record in walk(tree) if record.get("type") == "color_def")
    for record in templates:
        objects = record.get("subobjects")
        if (
            record.get("selector") != "tmBAR"
            or record.get("variation_code") != 3
            or record.get("template_specific_options") != 0
        ):
            continue
        if not isinstance(objects, list) or len(objects) < 3:
            continue
        left, right = objects[-2:]
        if not (_is_plain_bar_marker(left, "0xEC07") and _is_plain_bar_marker(right, "0xEC08")):
            continue

        prefix = objects[:-2]
        kinds = [item.get("type") for item in prefix]
        if kinds.count("line") != 1 or len(kinds) != len(set(kinds)):
            continue
        if any(kind not in {"line", "color", "size"} for kind in kinds):
            continue
        line_index = kinds.index("line")
        line = prefix[line_index]
        if not isinstance(line.get("objects"), list) or not line["objects"]:
            continue
        if "color" in kinds:
            color = prefix[kinds.index("color")]
            color_index = color.get("color_def_index")
            if (
                not isinstance(color_index, int)
                or color_index < 0
                or color_index > color_definitions
            ):
                continue
        if "size" in kinds:
            size = prefix[kinds.index("size")]
            if size.get("size_type") != 10 or kinds.index("size") < line_index:
                continue
        if "color" in kinds and kinds.index("color") not in {0, line_index + 1}:
            continue

        del objects[-2:]
    for record in walk(tree):
        if record.get("type") == "char" and record.get("mt_code_value") in {"0xEC07", "0xEC08"}:
            raise FormulaSourceError("unmapped_private_mtef_character")


def _is_plain_bar_marker(record: dict[str, Any], code: str) -> bool:
    return (
        record.get("type") == "char"
        and record.get("mt_code_value") == code
        and record.get("typeface") == 22
        and record.get("variation") == "mathmode"
        and record.get("options") == 0
        and not record.get("embellishment_list")
    )


def convert_ole_formula(ole_bytes: bytes) -> FormulaConversion:
    """Convert one OLE object; unsupported content is an explicit result."""
    if not isinstance(ole_bytes, bytes):
        raise TypeError("ole_bytes must be bytes")
    source_sha = _sha(ole_bytes)
    signature = extract_native_equation(ole_bytes)
    if signature["status"] != "valid":
        return _unsupported(
            "ole", source_sha, signature["reason_code"] or "native_equation_unavailable"
        )
    payload_sha = signature["payload_sha256"]
    try:
        native = _ole_native_stream(ole_bytes)
        mtef = native[28:]
        if _sha(mtef) != payload_sha:
            raise FormulaSourceError("native_payload_hash_mismatch")
        root = _strict_parse_mtef(mtef)
        latex, mathml = _convert_mathml(root)
        return FormulaConversion(
            source_kind="ole",
            source_sha256=source_sha,
            native_payload_sha256=payload_sha,
            status="converted",
            latex=latex,
            mathml=mathml,
            converter=_CONVERTER,
            errors=[],
        )
    except FormulaSourceError as exc:
        return _unsupported("ole", source_sha, exc.code, payload_sha=payload_sha)
    except Exception:
        return _unsupported("ole", source_sha, "conversion_failed", payload_sha=payload_sha)


def convert_omml_formula(source_xml: str) -> FormulaConversion:
    """Convert a frozen OMML source fragment using the audited OMML renderer."""
    if not isinstance(source_xml, str):
        raise TypeError("source_xml must be str")
    raw = source_xml.encode("utf-8")
    source_sha = _sha(raw)
    try:
        from xml.etree import ElementTree

        from app.source_math import render_omml

        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
        )
        node = etree.fromstring(raw, parser=parser)
        if node.getroottree().docinfo.doctype:
            raise FormulaSourceError("omml_doctype_forbidden")
        omml_ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
        for rad in node.iter():
            if etree.QName(rad).namespace != omml_ns or etree.QName(rad).localname != "rad":
                continue
            properties = rad.find(f"{{{omml_ns}}}radPr")
            if properties is None:
                continue
            hidden_degree = any(
                item.get(f"{{{omml_ns}}}val") in {"1", "true", "on"}
                for item in properties.findall(f"{{{omml_ns}}}degHide")
            )
            if hidden_degree:
                for degree in rad.findall(f"{{{omml_ns}}}deg"):
                    if degree.attrib or (degree.text and degree.text.strip()) or list(degree):
                        raise FormulaSourceError("omml_hidden_degree_not_empty")
                    rad.remove(degree)
        safe_xml = etree.tostring(node, encoding="unicode")
        rendered = render_omml(ElementTree.fromstring(safe_xml))
        if rendered.get("supported") is not True:
            raise FormulaSourceError("omml_structure_unsupported")
        mathml_root = etree.fromstring(
            rendered["html"].encode("utf-8"),
            parser=etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False),
        )
        latex, mathml = _convert_mathml(mathml_root)
        return FormulaConversion(
            source_kind="omml",
            source_sha256=source_sha,
            native_payload_sha256=None,
            status="converted",
            latex=latex,
            mathml=mathml,
            converter="source_math+strict-mathml-v1",
            errors=[],
        )
    except FormulaSourceError as exc:
        return _unsupported("omml", source_sha, exc.code)
    except Exception:
        return _unsupported("omml", source_sha, "omml_parse_failed")


def _safe_relative(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FormulaSourceError("source_path_invalid")
    path = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if path != resolved_root and resolved_root not in path.parents:
        raise FormulaSourceError("source_path_outside_bank")
    return path


def _load_source_bank(bank_dir: Path) -> tuple[dict[str, Any], bytes]:
    path = _safe_relative(bank_dir, "bank.json")
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise FormulaSourceError("source_bank_unavailable") from exc
    if raw.get("schema_version") != "source-bank-v3":
        raise FormulaSourceError("source_bank_schema_unsupported")
    return raw, raw_bytes


def _validate_source_binding(
    question, source_bank, bank_dir: Path, raw_questions, raw_docs
) -> dict[str, Path]:
    source_payload = question.source_payload
    if canonical_hash(source_payload) != question.source_hash:
        raise FormulaSourceError("source_question_hash_mismatch")
    raw_question = raw_questions.get(question.id)
    if raw_question is None or canonical_hash(raw_question) != question.source_hash:
        raise FormulaSourceError("source_question_bank_mismatch")
    expected_documents: dict[str, Path] = {}
    doc_refs = {doc.id: doc for doc in question.documents}
    mentioned_ids = {
        block.get("document_id")
        for field in ("stem_blocks", "answer_blocks", "analysis_blocks")
        for block in source_payload.get(field, [])
    }
    if not mentioned_ids.issubset(doc_refs):
        raise FormulaSourceError("source_document_reference_missing")
    for doc_id in mentioned_ids:
        ref = doc_refs[doc_id]
        source_doc = raw_docs.get(doc_id)
        if source_doc is None or source_doc.get("sha256") != ref.sha256:
            raise FormulaSourceError("source_document_metadata_mismatch")
        allowed = {copy.get("path") for copy in source_doc.get("source_copies", [])}
        if ref.path not in allowed:
            raise FormulaSourceError("source_document_path_mismatch")
        path = _safe_relative(bank_dir, ref.path)
        try:
            digest = _sha(path.read_bytes())
        except OSError as exc:
            raise FormulaSourceError("source_document_unavailable") from exc
        if digest != ref.sha256:
            raise FormulaSourceError("source_document_hash_mismatch")
        expected_documents[doc_id] = path
    asset_hashes = question.asset_hashes
    for asset_path, digest in asset_hashes.items():
        path = _safe_relative(bank_dir, asset_path)
        try:
            if _sha(path.read_bytes()) != digest:
                raise FormulaSourceError("source_asset_hash_mismatch")
        except OSError as exc:
            raise FormulaSourceError("source_asset_unavailable") from exc
    for field in ("stem_blocks", "answer_blocks", "analysis_blocks"):
        for block in source_payload.get(field, []):
            doc_id = block.get("document_id")
            document = expected_documents.get(doc_id)
            if document is None:
                raise FormulaSourceError("source_document_reference_missing")
            for occurrence in block.get("media_occurrences", []):
                asset_path = occurrence.get("media_path")
                expected_digest = asset_hashes.get(asset_path)
                source_part = occurrence.get("source_part")
                part = PurePosixPath(source_part or "")
                if (
                    not expected_digest
                    or part.is_absolute()
                    or part.parts[:2] != ("word", "media")
                    or any(value in {".", ".."} for value in part.parts)
                ):
                    raise FormulaSourceError("source_media_binding_invalid")
                try:
                    with zipfile.ZipFile(document) as archive:
                        media_bytes = archive.read(part.as_posix())
                except (OSError, KeyError, zipfile.BadZipFile) as exc:
                    raise FormulaSourceError("source_media_part_unavailable") from exc
                if _sha(media_bytes) != expected_digest:
                    raise FormulaSourceError("source_media_part_hash_mismatch")
    return expected_documents


def _extract_occurrence_bytes(document: Path, source_part: str) -> bytes:
    part = PurePosixPath(source_part)
    if (
        part.is_absolute()
        or part.parts[:2] != ("word", "embeddings")
        or any(p in {"..", "."} for p in part.parts)
    ):
        raise FormulaSourceError("ole_source_part_invalid")
    try:
        with zipfile.ZipFile(document) as archive:
            return archive.read(part.as_posix())
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise FormulaSourceError("ole_source_part_unavailable") from exc


def _formula_occurrences(question, docs: dict[str, Path]) -> dict[str, FormulaConversion]:
    found: dict[str, FormulaConversion] = {}
    source_payload = question.source_payload
    fields = ("stem_blocks", "answer_blocks", "analysis_blocks")
    for field in fields:
        for block in source_payload.get(field, []):
            document_id = block.get("document_id")
            document = docs.get(document_id)
            if document is None:
                raise FormulaSourceError("formula_document_unavailable")
            for occurrence in block.get("ole_occurrences", []):
                raw = _extract_occurrence_bytes(document, occurrence.get("source_part", ""))
                source_sha = _sha(raw)
                signature = extract_native_equation(raw)
                metadata = occurrence.get("native_equation") or {}
                if (
                    signature.get("status") != "valid"
                    or metadata.get("status") != "valid"
                    or signature.get("ole_sha256") != metadata.get("ole_sha256")
                    or signature.get("payload_sha256") != metadata.get("payload_sha256")
                    or source_sha != metadata.get("ole_sha256")
                ):
                    raise FormulaSourceError("ole_occurrence_binding_mismatch")
                converted = convert_ole_formula(raw)
                previous = found.get(source_sha)
                if previous is not None and previous.model_dump(
                    mode="json"
                ) != converted.model_dump(mode="json"):
                    raise FormulaSourceError("formula_hash_collision")
                found[source_sha] = converted
            for occurrence in block.get("omml_occurrences", []):
                source_xml = occurrence.get("source_xml")
                if not isinstance(source_xml, str):
                    raise FormulaSourceError("omml_source_missing")
                converted = convert_omml_formula(source_xml)
                previous = found.get(converted.source_sha256)
                if previous is not None and previous.model_dump(
                    mode="json"
                ) != converted.model_dump(mode="json"):
                    raise FormulaSourceError("formula_hash_collision")
                found[converted.source_sha256] = converted
    return found


def _snapshot_id(snapshot: BankPracticeSnapshot) -> str:
    return canonical_hash(snapshot.model_dump(mode="json"))


def build_formula_catalog(snapshot: BankPracticeSnapshot, bank_dir: Path) -> FormulaCatalog:
    """Convert every native formula occurrence in this frozen snapshot."""
    bank_dir = Path(bank_dir)
    source_bank, _ = _load_source_bank(bank_dir)
    raw_questions = {item["id"]: item for item in source_bank.get("questions", [])}
    raw_docs = {item["id"]: item for item in source_bank.get("documents", [])}
    entries: dict[str, FormulaConversion] = {}
    for item in snapshot.items:
        question = item.question
        docs = _validate_source_binding(question, source_bank, bank_dir, raw_questions, raw_docs)
        formulas = _formula_occurrences(question, docs)
        for source_sha, converted in formulas.items():
            previous = entries.get(source_sha)
            if previous is not None and previous.model_dump(mode="json") != converted.model_dump(
                mode="json"
            ):
                raise FormulaSourceError("formula_hash_collision")
            entries[source_sha] = converted
    return FormulaCatalog(
        snapshot_id=_snapshot_id(snapshot),
        bank_sha256=snapshot.bank_sha256,
        entries=entries,
    )


def validate_formula_catalog(
    snapshot: BankPracticeSnapshot, catalog: FormulaCatalog, bank_dir: Path
) -> None:
    """Rebuild source bindings and reject stale, missing, extra or altered entries."""
    expected = build_formula_catalog(snapshot, bank_dir)
    if catalog.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise ValueError("formula catalog differs from frozen source conversion")
