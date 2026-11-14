"""Transactional source-bound LaTeX/PDF export for fixed practice snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from PIL import Image

from app.bank_practice import (
    validate_bank_curriculum,
    validate_bank_progress,
    validate_bank_snapshot,
)
from app.bank_practice_contracts import BankPracticeSnapshot, PracticeBank
from app.contracts import Curriculum, StudentProgress
from app.exporting import ConverterUnavailable, ExportError, _available_executable
from app.latex_contracts import FormulaCatalog, FormulaConversion, validate_math_latex
from app.semantic_bank import canonical_hash
from app.source_exporting import (
    _contained,
    _file_sha256,
    _inline_option_columns,
    _normalise_model,
    _option_columns,
    _option_label,
    _question_units,
    _safe_html,
    _strip_element_prefix,
    _validate_snapshot_membership,
    _validate_source_bank,
)
from app.tool_client import ToolClientError, convert_tex_pdf, tools_enabled

LayoutMode = Literal["answer_space", "compact"]
_IMAGE_SUFFIX = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "TIFF": ".tif"}
_TEACHER_PREFIX_RE = re.compile(r"^\s*(?:【答案】|【解析】|【分析】|【详解】|【点睛】)\s*")
_INLINE_CALC_RE = re.compile(r"^\s*(?:计算|原式|解(?:\s*[：:]\s*原式)?)\s*[：:]?\s*$")
_TRAILING_PUNCTUATION = " \t\r\n。；;，,、：:"
_NUMERIC_CELL_RE = re.compile(r"^[+\-−]?(?:\d+(?:\.\d*)?|\.\d+)$")
_NUMERIC_LATEX_WRAPPER_RE = re.compile(
    r"^\\(?:mathbf|boldsymbol|mathit|mathrm|mathnormal|text)\{([+\-−]?(?:\d+(?:\.\d*)?|\.\d+))\}$"
)
_OPTION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-D])[.．、)]\s*")
_MAX_TEX_COMPILE_SECONDS = 90
_MAX_TEX_PASSES = 2
_TEXT_MATH_SYMBOLS = {
    "∠": r"\angle",
    "∴": r"\therefore",
    "∵": r"\because",
    "⊙": r"\odot",
    "⊥": r"\perp",
    "△": r"\triangle",
}
_EMPTY_PARENS_RE = re.compile(r"(?:（[ \t\r\n\u00a0\u3000]+）|\([ \t\r\n\u00a0\u3000]+\))")


def _sha256(data: bytes) -> str:  # type: ignore[no-redef]
    return hashlib.sha256(data).hexdigest()


def _tex_text(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "#": r"\#",
        "$": r"\$",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    pieces: list[str] = []
    text_run: list[str] = []

    def flush_text() -> None:
        if text_run:
            pieces.append("".join(replacements.get(char, char) for char in text_run))
            text_run.clear()

    index = 0
    while index < len(value):
        blank_parens = _EMPTY_PARENS_RE.match(value, index)
        if blank_parens is not None:
            flush_text()
            token = blank_parens.group()
            if token.startswith("（"):
                pieces.append(r"\mbox{（\hspace{1em}）}")
            else:
                pieces.append(r"\mbox{(\hspace{1em})}")
            index = blank_parens.end()
            continue
        char = value[index]
        command = _TEXT_MATH_SYMBOLS.get(char)
        if command is None:
            text_run.append(char)
            index += 1
            continue
        flush_text()
        pieces.append(r"\(" + command + r"\)")
        index += 1
    flush_text()
    return "".join(pieces)


def _safe_part(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise ExportError("source package part is invalid")
    part = PurePosixPath(value)
    if part.is_absolute() or any(segment in {"", ".", ".."} for segment in part.parts):
        raise ExportError("source package part escapes the DOCX")
    normalized = part.as_posix()
    if not normalized.startswith(prefix):
        raise ExportError("source package part has an unexpected type")
    return normalized


def _plain_text(node) -> str:
    pieces = [node.text or ""]
    for child in node:
        if str(child.tag).lower() == "br":
            pieces.append("\n")
        else:
            pieces.append(_plain_text(child))
        pieces.append(child.tail or "")
    return "".join(pieces)


def _inline_style(content: str, tag: str) -> str:
    if tag in {"strong", "b"}:
        return rf"\textbf{{{content}}}"
    if tag in {"em", "i"}:
        return rf"\emph{{{content}}}"
    if tag == "sup":
        return rf"\textsuperscript{{{content}}}"
    if tag == "sub":
        return rf"\textsubscript{{{content}}}"
    return content


class _LatexRenderer:
    def __init__(
        self,
        *,
        bank_root: Path,
        documents: dict[str, Path],
        asset_hashes: dict[str, str],
        catalog: FormulaCatalog,
        stage: Path,
    ) -> None:
        self.bank_root = bank_root
        self.documents = documents
        self.asset_hashes = asset_hashes
        self.catalog = catalog
        self.stage = stage
        self.used_formula_keys: set[str] = set()
        self.asset_records: dict[str, dict[str, Any]] = {}
        self.refs: list[dict[str, Any]] = []
        self.counts: dict[str, int] = defaultdict(int)
        (stage / "assets").mkdir(parents=True, exist_ok=True)

    def render_block(
        self,
        block: dict[str, Any],
        *,
        role: str,
        source_number: str | None = None,
        teacher_labels: bool = False,
        inline_option_columns: int | None = None,
    ) -> str:
        if not isinstance(block, dict) or not isinstance(block.get("html"), str):
            raise ExportError("source content block HTML is missing")
        root = _safe_html(block["html"])
        media = block.get("media_occurrences", [])
        omml = block.get("omml_occurrences", [])
        if not isinstance(media, list) or not isinstance(omml, list):
            raise ExportError("source formula or image occurrence records are invalid")
        context: dict[str, Any] = {
            "block": block,
            "role": role,
            "media": media,
            "omml": omml,
            "used_media": set(),
            "used_omml": set(),
            "used_ole_ids": set(),
            "option_columns": inline_option_columns,
            "option_next": 0,
        }
        self.refs.append(
            {
                "kind": "source_block",
                "role": role,
                "document_id": block.get("document_id"),
                "paragraph_index": block.get("paragraph_index"),
                "source_text_range": block.get("source_text_range"),
                "segment_index": block.get("segment_index"),
            }
        )
        chunks = []
        first_paragraph = True
        for child in root:
            tag = str(child.tag).lower()
            if tag in {"p", "div"}:
                if first_paragraph and source_number is not None:
                    _strip_source_number_node(child, source_number)
                if first_paragraph and teacher_labels:
                    _strip_element_prefix(child, _TEACHER_PREFIX_RE)
                chunks.append(self._render_paragraph(child, context))
                first_paragraph = False
            elif tag == "table":
                chunks.append(self._render_table(child, context))
                first_paragraph = False
            else:
                raise ExportError("unsupported top-level source markup")
            if child.tail and child.tail.strip():
                chunks.append(self._render_text(child.tail, context) + r"\par" + "\n")
        if not chunks and str(block.get("text", "")).strip():
            raise ExportError("source block has text but no renderable HTML content")
        if context["used_media"] != set(range(len(media))):
            raise ExportError("source image occurrence is missing from ordered HTML")
        if context["used_omml"] != set(range(len(omml))):
            raise ExportError("source OMML occurrence is missing from ordered HTML")
        return "".join(chunks)

    def _render_paragraph(self, node, context) -> str:
        children = list(node)
        if (
            len(children) == 1
            and str(children[0].tag).lower() == "img"
            and _INLINE_CALC_RE.fullmatch(node.text or "")
            and not (children[0].tail or "").strip(_TRAILING_PUNCTUATION)
        ):
            image = children[0]
            try:
                height_px = float(image.get("height", "0"))
            except ValueError:
                height_px = 0
            media_index = self._find_media(context, image.get("src", ""))
            occurrence = context["media"][media_index]
            if occurrence.get("kind") == "ole_preview" and height_px >= 27:
                formula = self._render_formula_image(image, context, media_index)
                intro = _tex_text(node.text or "")
                suffix = (children[0].tail or "").strip()
                if suffix:
                    formula += r"\text{" + _tex_text(suffix) + "}"
                return (
                    intro
                    + r"\par\smallskip"
                    + "\n"
                    + r"\["
                    + "\n"
                    + formula
                    + "\n"
                    + r"\]"
                    + r"\par"
                    + "\n"
                )

        result = self._render_text(node.text or "", context)
        for child in children:
            tag = str(child.tag).lower()
            if tag == "br":
                result += r"\newline "
            elif tag == "img":
                result += self._render_image(child, context)
            elif tag == "math" or (
                tag == "span" and "unsupported-omml" in child.get("class", "").split()
            ):
                result += self._render_omml(context)
            elif tag in {"p", "div", "span", "strong", "em", "b", "i", "sup", "sub"}:
                nested = (
                    self._render_paragraph(child, context)
                    if tag in {"p", "div"}
                    else self._render_inline(child, context)
                )
                result += _inline_style(nested, tag)
            elif tag == "table":
                result += self._render_table(child, context)
            else:
                raise ExportError("unsupported source markup element")
            result += self._render_text(child.tail or "", context)
        return result + r"\par" + "\n"

    def _render_inline(self, node, context) -> str:
        result = self._render_text(node.text or "", context)
        for child in node:
            tag = str(child.tag).lower()
            if tag == "br":
                piece = r"\newline "
            elif tag == "img":
                piece = self._render_image(child, context)
            elif tag == "math" or (
                tag == "span" and "unsupported-omml" in child.get("class", "").split()
            ):
                piece = self._render_omml(context)
            elif tag in {"span", "strong", "em", "b", "i", "sup", "sub"}:
                piece = _inline_style(self._render_inline(child, context), tag)
            elif tag in {"p", "div"}:
                piece = self._render_paragraph(child, context)
            elif tag == "table":
                piece = self._render_table(child, context)
            else:
                raise ExportError("unsupported nested source markup element")
            result += piece + self._render_text(child.tail or "", context)
        return result

    @staticmethod
    def _render_text(value: str, context) -> str:
        columns = context.get("option_columns")
        if not columns:
            return _tex_text(value)
        expected = context["option_next"]
        cursor = 0
        pieces = []
        for match in _OPTION_TOKEN_RE.finditer(value):
            label = match.group(1)
            if expected >= 4 or label != "ABCD"[expected]:
                continue
            prefix = value[cursor : match.start()]
            if expected == 0:
                pieces.append(_tex_text(prefix.lstrip()))
            elif prefix.strip():
                pieces.append(_tex_text(prefix.rstrip()))
            separator = (
                "" if expected == 0 else r"\hspace{\fill}" if expected % columns else r"\\[1mm]"
            )
            pieces.append(separator + _tex_text(f"{label}. "))
            cursor = match.end()
            expected += 1
            context["option_next"] = expected
        pieces.append(_tex_text(value[cursor:]))
        return "".join(pieces)

    def _find_media(self, context, relative: str) -> int:
        matches = [
            index
            for index, occurrence in enumerate(context["media"])
            if index not in context["used_media"] and occurrence.get("media_path") == relative
        ]
        if not matches:
            raise ExportError("image markup has no remaining ordered source-media binding")
        return matches[0]

    def _render_image(self, node, context) -> str:
        media_index = self._find_media(context, node.get("src", ""))
        occurrence = context["media"][media_index]
        if occurrence.get("kind") == "ole_preview":
            latex = self._render_formula_image(node, context, media_index)
            return rf"\({latex}\)" if latex else ""
        if occurrence.get("kind") != "drawingml" or occurrence.get("rendered") is not True:
            raise ExportError("unsupported or missing source image cannot be exported")
        context["used_media"].add(media_index)
        relative = occurrence.get("media_path")
        if not isinstance(relative, str):
            raise ExportError("geometry image path is missing")
        source = _contained(self.bank_root, relative)
        expected = self.asset_hashes.get(relative)
        if not expected or _file_sha256(source) != expected:
            raise ExportError("geometry image differs from frozen source asset")
        try:
            with Image.open(source) as image:
                image.verify()
                image_format = image.format
        except (OSError, Image.DecompressionBombError) as exc:
            raise ExportError("source geometry image is unreadable") from exc
        suffix = _IMAGE_SUFFIX.get(str(image_format).upper())
        if suffix is None:
            raise ExportError("unsupported source geometry image format")
        try:
            with Image.open(source) as image:
                pixel_width, pixel_height = image.size
            display_width = float(node.get("width")) if node.get("width") else float(pixel_width)
            display_height = (
                float(node.get("height")) if node.get("height") else float(pixel_height)
            )
        except (OSError, ValueError) as exc:
            raise ExportError("source geometry image dimensions are invalid") from exc
        if display_width <= 0 or display_height <= 0:
            raise ExportError("source geometry image has invalid display dimensions")
        name = f"geometry-{expected[:16]}{suffix}"
        destination = self.stage / "assets" / name
        if not destination.exists():
            shutil.copyfile(source, destination)
        self.asset_records[name] = {"path": f"assets/{name}", "sha256": expected}
        self.refs.append(
            {
                "kind": "geometry_image",
                "role": context["role"],
                "document_id": context["block"].get("document_id"),
                "paragraph_index": occurrence.get("paragraph_index"),
                "media_path": relative,
                "sha256": expected,
            }
        )
        self.counts[f"{context['role']}_geometry_image_count"] += 1
        return rf"\sourceimage{{assets/{name}}}{{{display_width * 0.75:.3f}}}{{{display_height * 0.75:.3f}}}"

    def _render_formula_image(self, node, context, media_index: int) -> str:
        occurrence = context["media"][media_index]
        relative = occurrence.get("media_path")
        ole_id = occurrence.get("ole_id")
        matches = [
            item
            for item in context["block"].get("ole_occurrences", [])
            if item.get("ole_id") == ole_id
        ]
        if not ole_id or len(matches) != 1:
            raise ExportError("formula preview is not bound to one source OLE object")
        ole = matches[0]
        if occurrence.get("source_rel_id") not in ole.get(
            "preview_rel_ids", []
        ) or relative not in ole.get("preview_media_paths", []):
            raise ExportError("formula preview binding differs from source OLE metadata")
        doc_id = context["block"].get("document_id")
        source_doc = self.documents.get(doc_id)
        if source_doc is None:
            raise ExportError("formula source document is unavailable")
        source_part = _safe_part(ole.get("source_part"), "word/embeddings/")
        try:
            with zipfile.ZipFile(source_doc) as archive:
                native_bytes = archive.read(source_part)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise ExportError("native MathType bytes are unavailable") from exc
        source_hash = _sha256(native_bytes)
        native = ole.get("native_equation") or {}
        if (
            native.get("status") != "valid"
            or native.get("ole_sha256") != source_hash
            or occurrence.get("rendered") is not True
        ):
            raise ExportError("native MathType identity does not match the source package")
        context["used_media"].add(media_index)
        if ole_id in context["used_ole_ids"]:
            self.counts[f"{context['role']}_ole_preview_alias_count"] += 1
            return ""
        context["used_ole_ids"].add(ole_id)
        return self._formula(source_hash, "ole", native.get("payload_sha256"), context)

    def _render_omml(self, context) -> str:
        unused = [
            index for index in range(len(context["omml"])) if index not in context["used_omml"]
        ]
        if not unused:
            raise ExportError("MathML preview has no bound native OMML occurrence")
        index = unused[0]
        occurrence = context["omml"][index]
        source_xml = occurrence.get("source_xml")
        if not isinstance(source_xml, str) or not source_xml.strip():
            raise ExportError("source OMML XML is missing")
        key = _sha256(source_xml.encode("utf-8"))
        context["used_omml"].add(index)
        latex = self._formula(key, "omml", None, context)
        return rf"\({latex}\)"

    def _formula(self, source_hash: str, source_kind: str, native_hash: str | None, context) -> str:
        conversion = self.catalog.entries.get(source_hash)
        if conversion is None:
            raise ExportError("formula catalog is missing a source occurrence")
        conversion = FormulaConversion.model_validate(conversion.model_dump(mode="python"))
        if conversion.source_kind != source_kind or conversion.source_sha256 != source_hash:
            raise ExportError("formula catalog identity differs from the source occurrence")
        if source_kind == "ole" and conversion.native_payload_sha256 != native_hash:
            raise ExportError("formula catalog MTEF payload identity differs from source OLE")
        if conversion.status != "converted" or conversion.latex is None:
            raise ExportError("source formula conversion is unsupported")
        try:
            latex = validate_math_latex(conversion.latex)
        except ValueError as exc:
            raise ExportError("formula catalog contains unsafe or invalid math LaTeX") from exc
        if context.get("normalize_numeric_math"):
            wrapped = _NUMERIC_LATEX_WRAPPER_RE.fullmatch(latex)
            if wrapped:
                latex = wrapped.group(1)
        self.used_formula_keys.add(source_hash)
        self.counts[f"{context['role']}_formula_count"] += 1
        self.counts[f"{context['role']}_{source_kind}_formula_count"] += 1
        self.refs.append(
            {
                "kind": "formula",
                "role": context["role"],
                "document_id": context["block"].get("document_id"),
                "paragraph_index": context["block"].get("paragraph_index"),
                "source_kind": source_kind,
                "source_sha256": source_hash,
                "native_payload_sha256": native_hash,
                "converter": conversion.converter,
            }
        )
        return latex

    def _render_table(self, node, context) -> str:
        rows = node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
        if not rows:
            raise ExportError("source table contains no rows")
        parsed_rows = []
        for row in rows:
            cells = row.xpath("./td|./th")
            if not cells:
                raise ExportError("source table row has no cells")
            if any(
                _positive_span(cell.get("colspan")) != 1 or _positive_span(cell.get("rowspan")) != 1
                for cell in cells
            ):
                raise ExportError("LaTeX export does not support merged source-table cells")
            parsed_rows.append(cells)
        column_count = len(parsed_rows[0])
        if any(len(row) != column_count for row in parsed_rows):
            raise ExportError("source table rows have inconsistent cell counts")
        columns = _table_column_spec(column_count)
        body = [rf"\begin{{tabularx}}{{\linewidth}}{{{columns}}}", r"\toprule"]
        for row_index, cells in enumerate(parsed_rows):
            rendered = []
            for cell in cells:
                cell_text = _plain_text(cell).strip()
                has_math_or_image = bool(
                    cell.xpath(".//img|.//math|.//span[contains(@class, 'unsupported-omml')]")
                )
                previous_normalize = context.get("normalize_numeric_math", False)
                context["normalize_numeric_math"] = True
                value = self._render_inline(cell, context)
                context["normalize_numeric_math"] = previous_normalize
                if _NUMERIC_CELL_RE.fullmatch(cell_text) and not has_math_or_image:
                    value = rf"\({cell_text}\)"
                elif str(cell.tag).lower() == "th":
                    value = rf"\textbf{{{value}}}"
                rendered.append(value)
            body.append(" & ".join(rendered) + r" \\")
            body.append(r"\midrule" if row_index < len(parsed_rows) - 1 else r"\bottomrule")
        body.append(r"\end{tabularx}\par")
        self.counts[f"{context['role']}_table_count"] += 1
        return "\n".join(body) + "\n"


def _positive_span(value: str | None) -> int:
    if value is None:
        return 1
    if not value.isdigit() or not 1 <= int(value) <= 64:
        raise ExportError("source table has an invalid cell span")
    return int(value)


def _table_column_spec(column_count: int) -> str:
    if column_count <= 0:
        raise ExportError("source table has no columns")
    if column_count == 1:
        return r">{\raggedright\arraybackslash}X"
    first_weight = column_count * 0.34
    other_weight = (column_count - first_weight) / (column_count - 1)
    first = rf">{{\hsize={first_weight:.4f}\hsize\raggedright\arraybackslash}}X"
    numeric = rf">{{\hsize={other_weight:.4f}\hsize\centering\arraybackslash}}X"
    return first + " " + " ".join(numeric for _ in range(column_count - 1))


def _strip_source_number_node(node, source_number: str | None) -> None:
    if not source_number or not source_number.isdigit():
        return
    pattern = re.compile(rf"^\s*{re.escape(source_number)}\s*[.．、)](?!\d)\s*")
    _strip_element_prefix(node, pattern)


def _subquestion_count(blocks: list[dict[str, Any]]) -> int:
    labels = set()
    for block in blocks:
        for line in str(block.get("text", "")).splitlines():
            match = re.match(r"^\s*[（(](\d{1,2})[）)]", line)
            if match:
                labels.add(match.group(1))
    return max(1, len(labels))


def _question_type(payload: dict[str, Any]) -> str:
    return str(payload.get("question_type", ""))


def _is_short_table_question(blocks: list[Any]) -> bool:
    if not 1 <= len(blocks) <= 5 or any(not isinstance(block, dict) for block in blocks):
        return False
    html_values = [str(block.get("html", "")) for block in blocks]
    table_count = sum(len(re.findall(r"<table\b", html, re.IGNORECASE)) for html in html_values)
    row_count = sum(len(re.findall(r"<tr\b", html, re.IGNORECASE)) for html in html_values)
    prose_size = sum(len(str(block.get("text", ""))) for block in blocks)
    return table_count == 1 and 1 <= row_count <= 12 and prose_size <= 1000


def _is_short_choice_or_fill_blank(question_type: str, blocks: list[Any]) -> bool:
    recognized = {
        "single_choice",
        "multiple_choice",
        "fill_blank",
        "单选",
        "多选",
        "选择题",
        "填空题",
    }
    if question_type not in recognized or not 1 <= len(blocks) <= 5:
        return False
    if any(not isinstance(block, dict) for block in blocks):
        return False
    prose_size = sum(len(str(block.get("text", ""))) for block in blocks)
    table_count = sum(
        len(re.findall(r"<table\b", str(block.get("html", "")), re.IGNORECASE)) for block in blocks
    )
    geometry_count = 0
    for block in blocks:
        media = block.get("media_occurrences", [])
        if not isinstance(media, list):
            return False
        geometry_count += sum(
            1
            for occurrence in media
            if isinstance(occurrence, dict) and occurrence.get("kind") == "drawingml"
        )
    return prose_size <= 1200 and geometry_count <= 4 and table_count == 0


def _document_tex(
    *,
    snapshot,
    curriculum: Curriculum,
    renderer: _LatexRenderer,
    teacher: bool,
    layout: LayoutMode,
) -> str:
    title = "教师答案与解析" if teacher else "数学练习"
    parts = [
        r"\documentclass[UTF8,fontset=fandol,zihao=-4]{ctexart}",
        r"\usepackage{fontspec}",
        r"\usepackage{unicode-math}",
        r"\setmainfont{lmroman10-regular.otf}[BoldFont=lmroman10-bold.otf,ItalicFont=lmroman10-italic.otf,BoldItalicFont=lmroman10-bolditalic.otf]",
        r"\setmathfont{latinmodern-math.otf}",
        r"\usepackage[a4paper,margin=18mm,headheight=14pt,footskip=10mm]{geometry}",
        r"\usepackage{graphicx,tabularx,array,booktabs,enumitem,fancyhdr}",
        r"\newlength{\sourceimagewidth}",
        r"\newlength{\sourceimageheight}",
        r"\newcommand{\sourceimage}[3]{\begingroup\setlength{\sourceimagewidth}{#2pt}\setlength{\sourceimageheight}{#3pt}\ifdim\sourceimagewidth>\linewidth\setlength{\sourceimagewidth}{\linewidth}\fi\ifdim\sourceimageheight>.42\textheight\setlength{\sourceimageheight}{.42\textheight}\fi\includegraphics[width=\sourceimagewidth,height=\sourceimageheight,keepaspectratio]{#1}\endgroup}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{4pt}",
        r"\setlength{\emergencystretch}{2em}",
        r"\pagestyle{fancy}",
        r"\fancyhf{}",
        r"\fancyfoot[C]{\thepage}",
        r"\renewcommand{\headrulewidth}{0pt}",
        r"\begin{document}",
        r"\begin{center}{\LARGE\bfseries " + _tex_text(title) + r"}\end{center}",
    ]
    if not teacher:
        parts.append(
            r"姓名：\rule{42mm}{0.3pt}\quad 日期：\rule{30mm}{0.3pt}\hfill "
            + _tex_text("作答版" if layout == "answer_space" else "紧凑版")
            + r"\par"
        )
        if snapshot.is_synthetic_student:
            parts.append(r"{\small 合成学生进度演示 · 非真实学生\par}")
    active_point = None
    for item in snapshot.items:
        if item.target_point_id != active_point:
            point = next(
                (point for point in curriculum.points if point.id == item.target_point_id), None
            )
            if point is None:
                raise ExportError("practice target point is missing from curriculum")
            parts.append(
                r"\vspace{2mm}{\large\bfseries " + _tex_text(point.name) + r"}\par\nopagebreak"
            )
            active_point = item.target_point_id
        question = item.question
        payload = question.source_payload
        number = item.order
        source_number = str(payload.get("number")) if payload.get("number") is not None else None
        question_type = _question_type(payload)
        if teacher:
            sections = (
                ("答案：", payload.get("answer_blocks", [])),
                ("解析：", payload.get("analysis_blocks", [])),
            )
            emitted_number = False
            for label, blocks in sections:
                blocks = [block for block in blocks if isinstance(block, dict)]
                for block_index, block in enumerate(blocks):
                    body = renderer.render_block(
                        block,
                        role="teacher",
                        teacher_labels=True,
                    )
                    prefix = f"\\textbf{{{number}.}}\\quad " if not emitted_number else ""
                    if block_index == 0 and body.strip():
                        prefix += _tex_text(label)
                    parts.append(prefix + body)
                    emitted_number = True
            if not emitted_number:
                raise ExportError("teacher answer/analysis content is empty")
            continue

        stem_blocks = payload.get("stem_blocks", [])
        if not isinstance(stem_blocks, list) or not stem_blocks:
            raise ExportError("student stem content is empty")
        choice_types = {"single_choice", "multiple_choice", "单选", "多选", "选择题"}
        keep_question_together = _is_short_choice_or_fill_blank(
            question_type, stem_blocks
        ) or _is_short_table_question(stem_blocks)
        if keep_question_together:
            parts.append(r"\par\noindent\begin{minipage}{\linewidth}")
        units = _question_units(
            stem_blocks,
            "single_choice" if question_type in choice_types else question_type,
        )
        question_number_in_option_cell = bool(units and units[0][0] == "options")
        if not question_number_in_option_cell:
            parts.append(r"\textbf{" + str(number) + r".}\quad ")
        first_block = True
        for unit_kind, unit in units:
            if unit_kind == "options":
                columns = _option_columns(unit)
                col_spec = " ".join(r">{\raggedright\arraybackslash}X" for _ in range(columns))
                option_rows = []
                for row_start in range(0, len(unit), columns):
                    row_blocks = unit[row_start : row_start + columns]
                    cells = []
                    for block in row_blocks:
                        if not isinstance(block, dict) or _option_label(block) is None:
                            raise ExportError("choice option labels are unreliable")
                        rendered = renderer.render_block(
                            block,
                            role="student",
                            source_number=source_number if first_block else None,
                        )
                        if first_block and question_number_in_option_cell:
                            rendered = r"\textbf{" + str(number) + r".}\quad " + rendered
                        cells.append(rendered)
                        first_block = False
                    cells.extend([""] * (columns - len(cells)))
                    option_rows.append(" & ".join(cells) + r" \\")
                parts.append(
                    rf"\begin{{tabularx}}{{\linewidth}}{{{col_spec}}}"
                    + "\n"
                    + "\n".join(option_rows)
                    + "\n"
                    + r"\end{tabularx}\par"
                    + "\n"
                )
            else:
                block = unit
                if not isinstance(block, dict):
                    raise ExportError("source stem block is invalid")
                inline_columns = (
                    _inline_option_columns(block) if unit_kind == "inline_options" else None
                )
                parts.append(
                    renderer.render_block(
                        block,
                        role="student",
                        source_number=source_number if first_block else None,
                        inline_option_columns=inline_columns,
                    )
                )
                first_block = False
        if keep_question_together:
            parts.append(r"\end{minipage}\par")
        if question_type == "free_response" and layout == "answer_space":
            subquestions = min(_subquestion_count(stem_blocks), 6)
            lines = 7 + 3 * (subquestions - 1)
            parts.append(
                # Keep the last stem line with its answer area. A bare
                # vertical skip allows a short stem to orphan at the footer.
                "\\par\\nopagebreak[4]\\vspace{2mm}\\nopagebreak[4]"
                "\\begin{minipage}{\\linewidth}"
                "\\begingroup\\setlength{\\parskip}{0pt}\n"
            )
            for _ in range(lines):
                parts.append(
                    r"\noindent\vbox to 5.4mm{\hsize=\linewidth\vfil"
                    r"\hrule width \linewidth height 0.35pt\vfil}\par" + "\n"
                )
            parts.append("\\endgroup\\end{minipage}\\par\n")
    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


def _validate_catalog(
    snapshot, bank_dir: Path, practice_bank: PracticeBank, catalog: FormulaCatalog
) -> None:
    if catalog.snapshot_id != canonical_hash(snapshot.model_dump(mode="json")):
        raise ExportError("formula catalog belongs to a different practice snapshot")
    if catalog.bank_sha256 != snapshot.bank_sha256:
        raise ExportError("formula catalog belongs to a different practice-bank snapshot")
    try:
        from app.math_latex import validate_formula_catalog

        validate_formula_catalog(snapshot, catalog, bank_dir)
    except ImportError as exc:
        raise ConverterUnavailable("source-bound LaTeX formula converter is unavailable") from exc
    except (ValueError, TypeError, OSError) as exc:
        raise ExportError("formula catalog failed source revalidation") from exc


def _tex_log_failure(log_text: str) -> str | None:
    checks = (
        (r"Overfull \\[hv]box", "overfull TeX layout"),
        (r"Missing character:", "missing output glyph"),
        (r"Unicode character .* not set up", "unsupported Unicode glyph"),
        (r"Font .* not loadable", "font could not be loaded"),
        (r"! LaTeX Error:", "LaTeX reported an error"),
        (r"Emergency stop", "TeX compilation stopped early"),
    )
    for pattern, reason in checks:
        if re.search(pattern, log_text, re.IGNORECASE):
            return reason
    return None


def _tex_error_summary(log_text: str) -> str:
    errors = [
        re.sub(r"\s+", " ", line).strip()[:180]
        for line in log_text.splitlines()
        if line.startswith("!")
    ]
    return "; ".join(errors[-2:]) if errors else "engine returned a nonzero status"


def _compile_pdf(tex_path: Path, build_dir: Path, executable: str) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        f"-output-directory={build_dir}",
        tex_path.name,
    ]
    log_parts = []
    for _ in range(_MAX_TEX_PASSES):
        if tools_enabled():
            try:
                result = convert_tex_pdf(tex_path, build_dir)
            except ToolClientError as exc:
                raise ExportError(f"XeLaTeX remote compile failed: {exc}") from exc
        else:
            try:
                local = subprocess.run(
                    command,
                    cwd=tex_path.parent,
                    capture_output=True,
                    text=True,
                    timeout=_MAX_TEX_COMPILE_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ExportError("XeLaTeX compile timed out") from exc
            except OSError as exc:
                raise ConverterUnavailable("XeLaTeX could not be started") from exc
            result = {
                "returncode": local.returncode,
                "stdout": local.stdout or "",
                "stderr": local.stderr or "",
            }
        log_parts.extend((result["stdout"], result["stderr"]))
        log_path = build_dir / (tex_path.stem + ".log")
        if log_path.is_file():
            log_parts.append(log_path.read_text(encoding="utf-8", errors="replace"))
        if result["returncode"] != 0:
            raise ExportError(
                "XeLaTeX compilation failed: " + _tex_error_summary("\n".join(log_parts))
            )
    failure = _tex_log_failure("\n".join(log_parts))
    if failure:
        raise ExportError(f"XeLaTeX output rejected: {failure}")
    pdf_path = build_dir / f"{tex_path.stem}.pdf"
    if (
        not pdf_path.is_file()
        or pdf_path.stat().st_size < 8
        or not pdf_path.read_bytes().startswith(b"%PDF-")
    ):
        raise ExportError("XeLaTeX did not produce a valid PDF")
    return pdf_path


def export_latex_snapshot(
    snapshot: BankPracticeSnapshot,
    output_dir: str | Path,
    *,
    bank_dir: str | Path,
    practice_bank: PracticeBank,
    curriculum: Curriculum,
    current_progress: StudentProgress,
    formula_catalog: FormulaCatalog,
    xelatex_executable: str | Path | None,
    layout: LayoutMode = "answer_space",
) -> dict[str, Any]:
    """Render one immutable source-practice snapshot to student/teacher LaTeX and PDF."""
    snapshot = _normalise_model(snapshot, BankPracticeSnapshot)
    practice_bank = _normalise_model(practice_bank, PracticeBank)
    curriculum = _normalise_model(curriculum, Curriculum)
    current_progress = _normalise_model(current_progress, StudentProgress)
    formula_catalog = _normalise_model(formula_catalog, FormulaCatalog)
    if layout not in {"answer_space", "compact"}:
        raise ExportError("unsupported LaTeX layout mode")
    if not snapshot.items:
        raise ExportError("cannot export an empty source-practice snapshot")
    if snapshot.curriculum_version != curriculum.version:
        raise ExportError("snapshot and curriculum versions differ")
    _validate_snapshot_membership(snapshot, practice_bank)
    try:
        validate_bank_curriculum(practice_bank, curriculum)
        validate_bank_snapshot(snapshot, practice_bank)
        validate_bank_progress(snapshot, curriculum, current_progress)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExportError("snapshot, curriculum, or progress validation failed") from exc

    bank_root = Path(bank_dir).resolve(strict=True)
    documents = _validate_source_bank(bank_root, practice_bank, curriculum)
    _validate_catalog(snapshot, bank_root, practice_bank, formula_catalog)
    for item in snapshot.items:
        payload = item.question.source_payload
        if (
            item.question.status != "eligible"
            or payload.get("structure_status") != "imported"
            or payload.get("association_status") != "confirmed"
            or payload.get("answer_available") is not True
        ):
            raise ExportError("snapshot contains an ineligible source question")

    executable = "xelatex" if tools_enabled() else _available_executable(xelatex_executable)
    if not executable:
        raise ConverterUnavailable("XeLaTeX is required for LaTeX PDF export")
    final_dir = Path(output_dir).absolute()
    if final_dir.exists() or final_dir.is_symlink():
        raise ExportError("LaTeX export destination already exists")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    final_dir = final_dir.parent.resolve(strict=True) / final_dir.name
    if final_dir.exists() or final_dir.is_symlink():
        raise ExportError("LaTeX export destination already exists")
    try:
        final_dir.resolve(strict=False).relative_to(bank_root)
    except ValueError:
        pass
    else:
        raise ExportError("LaTeX export destination must be outside the source bank")
    stage = Path(tempfile.mkdtemp(prefix=f".{final_dir.name}-", dir=final_dir.parent))
    try:
        combined_asset_hashes = {
            key: value
            for item in snapshot.items
            for key, value in item.question.asset_hashes.items()
        }
        renderer = _LatexRenderer(
            bank_root=bank_root,
            documents=documents,
            asset_hashes=combined_asset_hashes,
            catalog=formula_catalog,
            stage=stage,
        )
        student_tex = stage / "student.tex"
        teacher_tex = stage / "teacher.tex"
        student_tex.write_text(
            _document_tex(
                snapshot=snapshot,
                curriculum=curriculum,
                renderer=renderer,
                teacher=False,
                layout=layout,
            ),
            encoding="utf-8",
        )
        student_counts = {
            key.removeprefix("student_"): value
            for key, value in renderer.counts.items()
            if key.startswith("student_")
        }
        student_refs = [ref for ref in renderer.refs if ref.get("role") == "student"]
        teacher_renderer = _LatexRenderer(
            bank_root=bank_root,
            documents=documents,
            asset_hashes=combined_asset_hashes,
            catalog=formula_catalog,
            stage=stage,
        )
        teacher_tex.write_text(
            _document_tex(
                snapshot=snapshot,
                curriculum=curriculum,
                renderer=teacher_renderer,
                teacher=True,
                layout=layout,
            ),
            encoding="utf-8",
        )
        teacher_counts = {
            key.removeprefix("teacher_"): value
            for key, value in teacher_renderer.counts.items()
            if key.startswith("teacher_")
        }
        teacher_refs = teacher_renderer.refs
        used_formula_keys = renderer.used_formula_keys | teacher_renderer.used_formula_keys
        if used_formula_keys != set(formula_catalog.entries):
            raise ExportError("formula catalog includes missing or unconsumed snapshot records")
        student_pdf_build = _compile_pdf(student_tex, stage / ".build-student", executable)
        teacher_pdf_build = _compile_pdf(teacher_tex, stage / ".build-teacher", executable)
        student_pdf = stage / "student.pdf"
        teacher_pdf = stage / "teacher.pdf"
        shutil.copyfile(student_pdf_build, student_pdf)
        shutil.copyfile(teacher_pdf_build, teacher_pdf)
        shutil.rmtree(stage / ".build-student", ignore_errors=True)
        shutil.rmtree(stage / ".build-teacher", ignore_errors=True)
        files = {
            "student_tex": student_tex,
            "student_pdf": student_pdf,
            "teacher_tex": teacher_tex,
            "teacher_pdf": teacher_pdf,
        }
        all_assets = {**renderer.asset_records, **teacher_renderer.asset_records}
        counts = {
            "student": {
                **student_counts,
                "source_ref_count": len(student_refs),
                "geometry_asset_count": len(renderer.asset_records),
            },
            "teacher": {
                **teacher_counts,
                "source_ref_count": len(teacher_refs),
                "geometry_asset_count": len(teacher_renderer.asset_records),
            },
        }
        manifest = {
            "schema_version": "source-latex-export-v1",
            "snapshot_id": canonical_hash(snapshot.model_dump(mode="json")),
            "source_bank_sha256": practice_bank.source_bank_sha256,
            "practice_bank_sha256": snapshot.bank_sha256,
            "formula_catalog_snapshot_id": formula_catalog.snapshot_id,
            "formula_catalog_bank_sha256": formula_catalog.bank_sha256,
            "layout": layout,
            "template_version": "latex-exam-v2",
            "teacher_content": "answers_and_solutions",
            "curriculum_version": curriculum.version,
            "current_progress_revision": current_progress.revision,
            "progress_validation": "passed",
            "is_synthetic_student": snapshot.is_synthetic_student,
            "item_count": len(snapshot.items),
            "formula_catalog_entry_count": len(formula_catalog.entries),
            "counts": counts,
            "files": {
                name: {"path": path.name, "sha256": _file_sha256(path)}
                for name, path in files.items()
            },
            "assets": sorted(all_assets.values(), key=lambda item: item["path"]),
            "source_refs": {"student": student_refs, "teacher": teacher_refs},
        }
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for path in files.values():
            if not path.is_file() or path.stat().st_size == 0:
                raise ExportError("LaTeX export artifact is missing or empty")
        for asset in stage.joinpath("assets").iterdir():
            if not asset.is_file() or asset.stat().st_size == 0:
                raise ExportError("LaTeX geometry asset is missing or empty")
        stage.rename(final_dir)
        return {
            "snapshot_id": manifest["snapshot_id"],
            "layout": layout,
            "files": {name: str(final_dir / path.name) for name, path in files.items()},
            "manifest_path": str(final_dir / "manifest.json"),
            "counts": counts,
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
