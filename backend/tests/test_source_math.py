from xml.etree import ElementTree as ET

from app.source_math import (
    M_NS,
    SUPPORTED_MATHML_ATTRIBUTES,
    SUPPORTED_MATHML_TAGS,
    SUPPORTED_MATHVARIANT_VALUES,
    render_omml,
)

M = f"{{{M_NS}}}"


def _node(name: str, text: str | None = None, children: list[ET.Element] | None = None):
    element = ET.Element(M + name)
    element.text = text
    for child in children or []:
        element.append(child)
    return element


def _run(text: str):
    return _node("r", children=[_node("t", text)])


def _expr(*children: ET.Element):
    return _node("e", children=list(children))


def test_renders_text_fraction_root_and_scripts_to_mathml() -> None:
    fraction = _node(
        "f",
        children=[
            _node("num", children=[_run("x+1")]),
            _node("den", children=[_run("2")]),
        ],
    )
    radical = _node("rad", children=[_expr(_run("9"))])
    script = _node(
        "sSubSup",
        children=[
            _expr(_run("a")),
            _node("sub", children=[_run("1")]),
            _node("sup", children=[_run("2")]),
        ],
    )
    result = render_omml(_node("oMath", children=[fraction, _run("="), radical, script]))

    assert result["supported"] is True
    assert result["issues"] == []
    assert result["text"] == "x+12=9a12"
    assert result["html"].startswith('<math xmlns="http://www.w3.org/1998/Math/MathML">')
    assert "<mfrac>" in result["html"]
    assert "<msqrt>" in result["html"]
    assert "<msubsup>" in result["html"]
    ET.fromstring(result["html"])


def test_math_text_is_escaped_and_source_xml_is_retained() -> None:
    source = _node("oMath", children=[_run("<img src=x>&")])

    result = render_omml(source)

    assert result["supported"] is True
    assert "&lt;" in result["html"] and "&amp;" in result["html"]
    assert "<img" not in result["html"]
    assert result["source_xml"] == ET.tostring(source, encoding="unicode")
    assert result["text"] == "<img src=x>&"


def test_unknown_structure_fails_visibly_without_dropping_m_text() -> None:
    unknown = _node("mystery", children=[_run("preserve this")])
    source = _node("oMath", children=[unknown])

    result = render_omml(source)

    assert result["supported"] is False
    assert result["issues"][0]["code"] == "unsupported_omml"
    assert "preserve this" in result["text"]
    assert "preserve this" in result["html"]
    assert "mystery" not in result["html"]
    assert "mystery" in result["source_xml"]


def test_unknown_attribute_fails_instead_of_being_ignored() -> None:
    source = _node("oMath", children=[_run("x")])
    source[0].set("onclick", "alert(1)")

    result = render_omml(source)

    assert result["supported"] is False
    assert result["issues"][0]["code"] == "unsupported_omml"
    assert "x" in result["text"]
    assert "x" in result["html"]
    assert "onclick" not in result["html"]


def test_only_plain_roman_maps_to_mathvariant_normal() -> None:
    plain = _node(
        "r",
        children=[
            _node("rPr", children=[ET.Element(M + "sty", {M + "val": "p"})]),
            _node("t", "abc"),
        ],
    )

    result = render_omml(_node("oMath", children=[plain]))

    assert result["supported"] is True
    assert '<mi mathvariant="normal">abc</mi>' in result["html"]
    assert SUPPORTED_MATHVARIANT_VALUES == frozenset({"normal"})

    italic = _node(
        "r",
        children=[
            _node("rPr", children=[ET.Element(M + "sty", {M + "val": "i"})]),
            _node("t", "x"),
        ],
    )
    italic_result = render_omml(_node("oMath", children=[italic]))
    assert italic_result["supported"] is True
    assert "<mi>x</mi>" in italic_result["html"]
    assert "mathvariant" not in italic_result["html"]


def test_bold_fraktur_and_multiletter_explicit_italic_fail_visibly() -> None:
    run = _node(
        "r",
        children=[
            _node(
                "rPr",
                children=[
                    ET.Element(M + "sty", {M + "val": "b"}),
                    ET.Element(M + "scr", {M + "val": "double-struck"}),
                ],
            ),
            _node("t", "x"),
        ],
    )

    bold_result = render_omml(_node("oMath", children=[run]))

    assert bold_result["supported"] is False
    assert bold_result["issues"][0]["code"] == "unsupported_omml"
    assert "x" in bold_result["text"]
    assert "x" in bold_result["html"]

    fraktur = _node(
        "r",
        children=[
            _node("rPr", children=[ET.Element(M + "scr", {M + "val": "fraktur"})]),
            _node("t", "z"),
        ],
    )
    fraktur_result = render_omml(_node("oMath", children=[fraktur]))
    assert fraktur_result["supported"] is False
    assert "z" in fraktur_result["text"]

    multi_italic = _node(
        "r",
        children=[
            _node("rPr", children=[ET.Element(M + "sty", {M + "val": "i"})]),
            _node("t", "xy"),
        ],
    )
    italic_result = render_omml(_node("oMath", children=[multi_italic]))
    assert italic_result["supported"] is False
    assert "xy" in italic_result["text"]


def test_delimiters_use_explicit_mathml_operators_and_preserve_empty_edges() -> None:
    delimiter = _node(
        "d",
        children=[
            _node(
                "dPr",
                children=[
                    ET.Element(M + "begChr", {M + "val": ""}),
                    ET.Element(M + "endChr", {M + "val": ""}),
                ],
            ),
            _expr(_run("x")),
        ],
    )
    result = render_omml(_node("oMath", children=[delimiter]))

    assert result["supported"] is True
    assert "<mfenced" not in result["html"]
    assert "<mo fence=" not in result["html"]
    assert "<mi>x</mi>" in result["html"]

    bracketed = _node(
        "d",
        children=[
            _node(
                "dPr",
                children=[
                    ET.Element(M + "begChr", {M + "val": "["}),
                    ET.Element(M + "endChr", {M + "val": "]"}),
                ],
            ),
            _expr(_run("x")),
        ],
    )
    bracketed_result = render_omml(_node("oMath", children=[bracketed]))
    assert '<mo fence="true" stretchy="true">[</mo>' in bracketed_result["html"]
    assert '<mo fence="true" stretchy="true">]</mo>' in bracketed_result["html"]
    assert SUPPORTED_MATHML_TAGS == frozenset(
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
    assert SUPPORTED_MATHML_ATTRIBUTES["mo"] == frozenset({"mathvariant", "fence", "stretchy"})


def test_nary_layout_is_explicitly_unsupported_and_preserves_all_text() -> None:
    nary = _node(
        "nary",
        children=[
            _node("naryPr", children=[ET.Element(M + "chr", {M + "val": "∑"})]),
            _node("sub", children=[_run("i=0")]),
            _node("sup", children=[_run("n")]),
            _expr(_run("a_i")),
        ],
    )

    result = render_omml(_node("oMath", children=[nary]))

    assert result["supported"] is False
    assert result["issues"][0]["code"] == "unsupported_omml"
    assert result["text"] == "i=0na_i"
    assert "i=0na_i" in result["html"]
    assert "∑" in result["source_xml"]


def test_wrong_root_is_explicitly_unsupported() -> None:
    result = render_omml(_node("r", children=[_node("t", "x")]))

    assert result["supported"] is False
    assert result["issues"] == [
        {"code": "unsupported_omml", "reason": "root must be m:oMath or m:oMathPara"}
    ]
    assert "x" in result["html"]
