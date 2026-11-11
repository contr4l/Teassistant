"""Render a conservative subset of WordprocessingML mathematics as MathML.

This module accepts parsed XML elements only. It never treats source text as HTML,
and unsupported structures are surfaced as an escaped placeholder while retaining
the original XML for review or a later renderer.
"""

from __future__ import annotations

import html
import unicodedata
from xml.etree import ElementTree

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_M = f"{{{M_NS}}}"
_XML_SPACE = f"{{{XML_NS}}}space"

# Public contract for the viewer's independent HTML sanitizer.
SUPPORTED_MATHML_TAGS = frozenset(
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
    }
)
SUPPORTED_MATHML_ATTRIBUTES = {
    "math": frozenset({"xmlns"}),
    "mi": frozenset({"mathvariant"}),
    "mn": frozenset({"mathvariant"}),
    "mo": frozenset({"mathvariant", "fence", "stretchy"}),
    "mtext": frozenset({"mathvariant"}),
    "mover": frozenset({"accent"}),
    "munder": frozenset({"accentunder"}),
}
SUPPORTED_MATHVARIANT_VALUES = frozenset({"normal"})


class _UnsupportedOMML(ValueError):
    pass


def _tag(element: ElementTree.Element) -> str:
    if not isinstance(element.tag, str) or not element.tag.startswith(_M):
        raise _UnsupportedOMML(f"unexpected element {element.tag!r}")
    return element.tag[len(_M) :]


def _check_attrs(
    element: ElementTree.Element, allowed: set[str] | frozenset[str] = frozenset()
) -> None:
    unknown = set(element.attrib) - set(allowed)
    if unknown:
        raise _UnsupportedOMML(f"unsupported attribute(s) on {_tag(element)}: {sorted(unknown)!r}")


def _check_container_text(element: ElementTree.Element) -> None:
    if element.text and element.text.strip():
        raise _UnsupportedOMML(f"unexpected text outside a math text run in {_tag(element)}")
    for child in element:
        if child.tail and child.tail.strip():
            raise _UnsupportedOMML(f"unexpected mixed text after {_tag(child)}")


def _children(
    element: ElementTree.Element, allowed: set[str]
) -> list[tuple[str, ElementTree.Element]]:
    _check_attrs(element)
    _check_container_text(element)
    result = []
    for child in element:
        name = _tag(child)
        if name not in allowed:
            raise _UnsupportedOMML(f"unsupported child {name!r} in {_tag(element)}")
        result.append((name, child))
    return result


def _one(children: list[tuple[str, ElementTree.Element]], name: str, *, required: bool = True):
    found = [child for child_name, child in children if child_name == name]
    if len(found) > 1 or (required and len(found) != 1):
        raise _UnsupportedOMML(f"expected {'one' if required else 'at most one'} {name}")
    return found[0] if found else None


def _property_values(
    element: ElementTree.Element | None,
    property_name: str,
    allowed_values: dict[str, frozenset[str] | None],
) -> dict[str, str | None]:
    if element is None:
        return {}
    if _tag(element) != property_name:
        raise _UnsupportedOMML(f"expected {property_name} properties")
    _check_attrs(element)
    _check_container_text(element)
    values: dict[str, str | None] = {}
    for child in element:
        name = _tag(child)
        if name not in allowed_values:
            raise _UnsupportedOMML(f"unsupported {property_name} property {name!r}")
        _check_attrs(child, {_M + "val"})
        _check_container_text(child)
        if len(child):
            raise _UnsupportedOMML(f"nested structure in {property_name}/{name} is unsupported")
        value = child.get(_M + "val")
        valid = allowed_values[name]
        if valid is not None and value is not None and value not in valid:
            raise _UnsupportedOMML(f"unsupported value {value!r} for {property_name}/{name}")
        if name in values:
            raise _UnsupportedOMML(f"duplicate {property_name} property {name!r}")
        values[name] = value
    return values


def _text_tokens(text: str, mathvariant: str | None = None) -> str:
    if any(unicodedata.category(char) == "Cc" and char not in "\t\n\r" for char in text):
        raise _UnsupportedOMML("control character in math text")
    output: list[str] = []
    variant = f' mathvariant="{html.escape(mathvariant, quote=True)}"' if mathvariant else ""
    current = ""
    current_kind = ""

    def flush() -> None:
        nonlocal current, current_kind
        if current:
            tag = {"letter": "mi", "number": "mn", "space": "mtext"}[current_kind]
            output.append(f"<{tag}{variant}>{html.escape(current)}</{tag}>")
            current = ""
            current_kind = ""

    for char in text:
        category = unicodedata.category(char)
        if char.isspace():
            kind = "space"
        elif category.startswith("L") or category.startswith("M"):
            kind = "letter"
        elif category.startswith("N"):
            kind = "number"
        else:
            flush()
            output.append(f"<mo{variant}>{html.escape(char)}</mo>")
            continue
        if current_kind != kind:
            flush()
            current_kind = kind
        current += char
    flush()
    return "".join(output)


_BOOL_VALUES = frozenset({"0", "1", "on", "off", "true", "false"})
_RUN_PROPERTIES = {
    "sty": frozenset({"p", "b", "i", "bi"}),
    "scr": frozenset({"roman", "script", "fraktur", "double-struck", "sans-serif", "monospace"}),
    "nor": _BOOL_VALUES,
    "lit": _BOOL_VALUES,
}
_DELIMITERS = frozenset({"(", ")", "[", "]", "{", "}", "|", "‖", "⟨", "⟩", "⌊", "⌋", "⌈", "⌉", ""})
_ACCENTS = frozenset({"¯", "→", "⃗", "̂", "̃", "˙", "¨", "ˇ", "˘", "˚", "̇", "̈"})


def _mathvariant(values: dict[str, str | None], text: str) -> str | None:
    style = values.get("sty") or "p"
    script = values.get("scr") or "roman"
    if script == "roman" and style == "p":
        return "normal"
    if script == "roman" and style == "i":
        if len(text) == 1 and unicodedata.category(text)[:1] == "L":
            # MathML mi is italic by default; do not emit a legacy italic variant.
            return None
        raise _UnsupportedOMML("roman italic is supported only for a single-letter identifier")
    raise _UnsupportedOMML(f"unsupported explicit run style {script!r}/{style!r}")


def _enabled(value: str | None) -> bool:
    return value is None or value in {"1", "on", "true"}


def _math_nodes(children: list[ElementTree.Element]) -> str:
    if not children:
        raise _UnsupportedOMML("empty math expression")
    return "".join(_convert(child) for child in children)


def _convert(element: ElementTree.Element) -> str:
    name = _tag(element)
    _check_attrs(element)

    if name in {"oMath", "oMathPara"}:
        children = _children(
            element,
            {
                "oMath",
                "oMathPara",
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        return f"<mrow>{_math_nodes([child for _, child in children])}</mrow>"

    if name == "r":
        children = _children(element, {"rPr", "t"})
        prop = _one(children, "rPr", required=False)
        values = _property_values(prop, "rPr", _RUN_PROPERTIES)
        texts = [child for child_name, child in children if child_name == "t"]
        if not texts:
            raise _UnsupportedOMML("math run has no text")
        text = "".join(_plain_text(child) for child in texts)
        variant = _mathvariant(values, text) if "sty" in values or "scr" in values else None
        if any(_enabled(values.get(name)) for name in ("nor", "lit") if name in values):
            attr = f' mathvariant="{html.escape(variant, quote=True)}"' if variant else ""
            return f"<mtext{attr}>{html.escape(text)}</mtext>"
        return _text_tokens(text, variant)

    if name == "f":
        children = _children(element, {"fPr", "num", "den"})
        props = _one(children, "fPr", required=False)
        values = _property_values(props, "fPr", {"type": frozenset({"bar"})})
        if values.get("type") not in (None, "bar"):
            raise _UnsupportedOMML("only bar fractions are supported")
        numerator = _one(children, "num")
        denominator = _one(children, "den")
        assert numerator is not None and denominator is not None
        num_children = _children(
            numerator,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        den_children = _children(
            denominator,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        return f"<mfrac><mrow>{_math_nodes([c for _, c in num_children])}</mrow><mrow>{_math_nodes([c for _, c in den_children])}</mrow></mfrac>"

    if name == "rad":
        children = _children(element, {"radPr", "deg", "e"})
        props = _one(children, "radPr", required=False)
        values = _property_values(props, "radPr", {"degHide": _BOOL_VALUES})
        degree = _one(children, "deg", required=False)
        expression = _one(children, "e")
        assert expression is not None
        hidden_degree = values.get("degHide") in {"1", "on", "true"}
        expr_children = _children(
            expression,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        expr_html = f"<mrow>{_math_nodes([c for _, c in expr_children])}</mrow>"
        if degree is None or hidden_degree:
            if degree is not None:
                raise _UnsupportedOMML("hidden radical degree content is unsupported")
            return f"<msqrt>{expr_html}</msqrt>"
        degree_children = _children(
            degree,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        return (
            f"<mroot>{expr_html}<mrow>{_math_nodes([c for _, c in degree_children])}</mrow></mroot>"
        )

    if name in {"sSub", "sSup", "sSubSup"}:
        names = {
            "sSub": {"sSubPr", "e", "sub"},
            "sSup": {"sSupPr", "e", "sup"},
            "sSubSup": {"sSubSupPr", "e", "sub", "sup"},
        }[name]
        children = _children(element, names)
        prop_name = {"sSub": "sSubPr", "sSup": "sSupPr", "sSubSup": "sSubSupPr"}[name]
        prop = _one(children, prop_name, required=False)
        _property_values(prop, prop_name, {"aln": _BOOL_VALUES})
        base = _one(children, "e")
        assert base is not None
        base_children = _children(
            base,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        expr = f"<mrow>{_math_nodes([c for _, c in base_children])}</mrow>"
        sub = _one(children, "sub", required=name != "sSup")
        sup = _one(children, "sup", required=name != "sSub")
        sub_html = ""
        sup_html = ""
        for target, tag in ((sub, "sub"), (sup, "sup")):
            if target is not None:
                target_children = _children(
                    target,
                    {
                        "r",
                        "f",
                        "rad",
                        "sSub",
                        "sSup",
                        "sSubSup",
                        "d",
                        "nary",
                        "func",
                        "acc",
                        "bar",
                        "groupChr",
                        "limLow",
                        "limUpp",
                    },
                )
                rendered = f"<mrow>{_math_nodes([c for _, c in target_children])}</mrow>"
                if tag == "sub":
                    sub_html = rendered
                else:
                    sup_html = rendered
        if name == "sSub":
            return f"<msub>{expr}{sub_html}</msub>"
        if name == "sSup":
            return f"<msup>{expr}{sup_html}</msup>"
        return f"<msubsup>{expr}{sub_html}{sup_html}</msubsup>"

    if name == "d":
        children = _children(element, {"dPr", "e"})
        props = _one(children, "dPr", required=False)
        values = _property_values(
            props,
            "dPr",
            {
                "begChr": _DELIMITERS,
                "endChr": _DELIMITERS,
                "sepChr": _DELIMITERS,
                "shp": frozenset({"centered", "match"}),
            },
        )
        expressions = [child for child_name, child in children if child_name == "e"]
        if not expressions:
            raise _UnsupportedOMML("delimiter has no expression")
        rendered = []
        for expression in expressions:
            expr_children = _children(
                expression,
                {
                    "r",
                    "f",
                    "rad",
                    "sSub",
                    "sSup",
                    "sSubSup",
                    "d",
                    "nary",
                    "func",
                    "acc",
                    "bar",
                    "groupChr",
                    "limLow",
                    "limUpp",
                },
            )
            rendered.append(f"<mrow>{_math_nodes([c for _, c in expr_children])}</mrow>")
        open_value = values["begChr"] if "begChr" in values else "("
        close_value = values["endChr"] if "endChr" in values else ")"
        separator = values["sepChr"] if "sepChr" in values else ","
        parts = []
        if open_value:
            parts.append(f'<mo fence="true" stretchy="true">{html.escape(open_value)}</mo>')
        for index, expression in enumerate(rendered):
            if index and separator:
                parts.append(f"<mo>{html.escape(separator)}</mo>")
            parts.append(expression)
        if close_value:
            parts.append(f'<mo fence="true" stretchy="true">{html.escape(close_value)}</mo>')
        return f"<mrow>{''.join(parts)}</mrow>"

    if name == "nary":
        raise _UnsupportedOMML("n-ary operators require a dedicated verified layout")

    if name == "func":
        children = _children(element, {"funcPr", "fName", "e"})
        _property_values(_one(children, "funcPr", required=False), "funcPr", {})
        fname = _one(children, "fName")
        expression = _one(children, "e")
        assert fname is not None and expression is not None
        fname_children = _children(fname, {"r"})
        expr_children = _children(
            expression,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        return f"<mrow><mrow>{_math_nodes([c for _, c in fname_children])}</mrow><mo>⁡</mo><mrow>{_math_nodes([c for _, c in expr_children])}</mrow></mrow>"

    if name == "acc":
        children = _children(element, {"accPr", "e"})
        props = _one(children, "accPr", required=False)
        values = _property_values(props, "accPr", {"chr": _ACCENTS})
        expression = _one(children, "e")
        assert expression is not None
        expr_children = _children(
            expression,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        accent = html.escape(values.get("chr") or "̂")
        return f'<mover accent="true"><mrow>{_math_nodes([c for _, c in expr_children])}</mrow><mo>{accent}</mo></mover>'

    if name == "bar":
        children = _children(element, {"barPr", "e"})
        props = _one(children, "barPr", required=False)
        values = _property_values(props, "barPr", {"pos": frozenset({"top", "bot"})})
        expression = _one(children, "e")
        assert expression is not None
        expr_children = _children(
            expression,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        if values.get("pos") == "bot":
            return f'<munder accentunder="true"><mrow>{_math_nodes([c for _, c in expr_children])}</mrow><mo>_</mo></munder>'
        return f'<mover accent="true"><mrow>{_math_nodes([c for _, c in expr_children])}</mrow><mo>¯</mo></mover>'

    if name == "groupChr":
        children = _children(element, {"groupChrPr", "e"})
        props = _one(children, "groupChrPr", required=False)
        values = _property_values(
            props,
            "groupChrPr",
            {
                "chr": frozenset({"⏞", "⏟", "⏜", "⏝"}),
                "pos": frozenset({"top", "bot"}),
            },
        )
        expression = _one(children, "e")
        assert expression is not None
        expr_children = _children(
            expression,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        glyph = html.escape(values.get("chr") or "⏞")
        if values.get("pos") == "bot":
            return f"<munder><mrow>{_math_nodes([c for _, c in expr_children])}</mrow><mo>{glyph}</mo></munder>"
        return f"<mover><mrow>{_math_nodes([c for _, c in expr_children])}</mrow><mo>{glyph}</mo></mover>"

    if name in {"limLow", "limUpp"}:
        prop_name = f"{name}Pr"
        children = _children(element, {prop_name, "e", "lim"})
        props = _one(children, prop_name, required=False)
        _property_values(props, prop_name, {})
        expression = _one(children, "e")
        limit = _one(children, "lim")
        assert expression is not None and limit is not None
        expr_children = _children(
            expression,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        lim_children = _children(
            limit,
            {
                "r",
                "f",
                "rad",
                "sSub",
                "sSup",
                "sSubSup",
                "d",
                "nary",
                "func",
                "acc",
                "bar",
                "groupChr",
                "limLow",
                "limUpp",
            },
        )
        tag = "munder" if name == "limLow" else "mover"
        return f"<{tag}><mrow>{_math_nodes([c for _, c in expr_children])}</mrow><mrow>{_math_nodes([c for _, c in lim_children])}</mrow></{tag}>"

    raise _UnsupportedOMML(f"unsupported OMML element {name!r}")


def _plain_text(element: ElementTree.Element) -> str:
    if _tag(element) != "t":
        raise _UnsupportedOMML("expected math text node")
    _check_attrs(element, {_XML_SPACE})
    if len(element):
        raise _UnsupportedOMML("math text element must not contain child elements")
    value = element.text or ""
    preserve = element.get(_XML_SPACE)
    if preserve not in (None, "default", "preserve"):
        raise _UnsupportedOMML(f"unsupported xml:space value {preserve!r}")
    if preserve != "preserve":
        value = value.strip()
    if not value:
        raise _UnsupportedOMML("empty math text element")
    return value


def _source_text(element: ElementTree.Element) -> str:
    values = []
    for node in element.iter():
        if isinstance(node.tag, str) and node.tag == _M + "t":
            values.append(node.text or "")
    return "".join(values)


def render_omml(element: ElementTree.Element) -> dict:
    """Render supported OMML to MathML, or return an explicit visible failure.

    The returned mapping always includes the serialized input XML. Unsupported
    elements, attributes, property values, or malformed structure never disappear
    silently; any descendant ``m:t`` text is included in both ``text`` and the
    escaped visible placeholder.
    """
    if not isinstance(element, ElementTree.Element):
        raise TypeError("render_omml expects an xml.etree.ElementTree.Element")
    source_xml = ElementTree.tostring(element, encoding="unicode")
    source_text = _source_text(element)
    try:
        if _tag(element) not in {"oMath", "oMathPara"}:
            raise _UnsupportedOMML("root must be m:oMath or m:oMathPara")
        body = _convert(element)
        return {
            "html": f'<math xmlns="http://www.w3.org/1998/Math/MathML">{body}</math>',
            "text": source_text,
            "source_xml": source_xml,
            "supported": True,
            "issues": [],
        }
    except _UnsupportedOMML as exc:
        visible = html.escape(source_text) or "公式结构暂不支持"
        reason = str(exc)
        return {
            "html": f'<span class="unsupported-omml">[不支持的公式：{visible}]</span>',
            "text": source_text,
            "source_xml": source_xml,
            "supported": False,
            "issues": [{"code": "unsupported_omml", "reason": reason}],
        }
