"""Editable source-native DOCX/PDF export for a frozen source-practice snapshot.

The exporter is deliberately fail-closed: source assets and OOXML relationships are
verified before use, and a four-file directory is published only after both PDFs
have been validated.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import tempfile
import unicodedata
import uuid
import zipfile
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Inches, Mm
from lxml import etree, html
from PIL import Image

from app.bank_practice_contracts import BankPracticeSnapshot, PracticeBank
from app.contracts import Curriculum, StudentProgress
from app.exporting import (
    ConverterUnavailable,
    ExportError,
    _available_executable,
    _convert_pdf,
)
from app.print_layout import (
    TEMPLATE_VERSION,
    LayoutMode,
    append_answer_space,
    make_exam_document,
    style_source_table,
)
from app.semantic_bank import canonical_hash
from app.tool_client import tools_enabled

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
W_P = f"{{{W}}}p"
W_R = f"{{{W}}}r"
W_T = f"{{{W}}}t"
_MARKER = "TSXMARKER"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPTION_LABEL_RE = re.compile(r"^\s*([A-D])[.．、)]\s*")
_TEACHER_LABEL_RE = re.compile(r"^\s*(?:【答案】|【解析】|【分析】|【详解】|【点睛】)\s*")
_DISPLAY_FORMULA_INTRO_RE = re.compile(
    r"^\s*(?:\d+[.．、)]\s*)?(?:计算|原式|化简|求值|证明|解(?:\s*[：:]\s*原式)?)\s*[：:]?\s*$"
)
_ALLOWED_HTML = {
    "p",
    "br",
    "span",
    "strong",
    "em",
    "b",
    "i",
    "sup",
    "sub",
    "img",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "tr",
    "td",
    "th",
}
_MATHML = {
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
_MATH_ATTRS = {
    "math": {"xmlns"},
    "mi": {"mathvariant"},
    "mn": {"mathvariant"},
    "mo": {"mathvariant", "fence", "stretchy"},
    "mtext": {"mathvariant"},
    "mover": {"accent"},
    "munder": {"accentunder"},
}
_SAFE_ATTRS = {
    "img": {"src", "alt", "width", "height", "style", "class", "loading"},
    "td": {"colspan", "rowspan", "style"},
    "th": {"colspan", "rowspan", "style"},
    "span": {"style", "class"},
    "p": {"style", "class"},
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ExportError("source path is invalid")
    parsed = urlparse(relative)
    if parsed.scheme or parsed.netloc or relative.startswith(("/", "//")):
        raise ExportError("remote or absolute source paths are not supported")
    candidate = (root / relative).resolve(strict=must_exist)
    root_resolved = root.resolve(strict=True)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ExportError("source path escapes its configured root") from exc
    if must_exist and not candidate.is_file():
        raise ExportError("source file is missing")
    return candidate


def _safe_html(fragment: str):
    wrapper = html.fragment_fromstring(fragment or "", create_parent="div")
    for node in wrapper.iterdescendants():
        tag = str(node.tag).lower()
        if tag in _MATHML:
            if any(key not in _MATH_ATTRS.get(tag, set()) for key in node.attrib):
                raise ExportError("unsupported MathML attribute")
            for key, value in node.attrib.items():
                allowed = {
                    "xmlns": {"http://www.w3.org/1998/Math/MathML"},
                    "mathvariant": {"normal"},
                    "fence": {"true", "false"},
                    "stretchy": {"true", "false"},
                    "accent": {"true", "false"},
                    "accentunder": {"true", "false"},
                }.get(key)
                if allowed is None or value not in allowed:
                    raise ExportError("unsupported MathML attribute value")
            continue
        if tag not in _ALLOWED_HTML:
            raise ExportError(f"unsupported source markup element: {tag}")
        allowed = _SAFE_ATTRS.get(tag, set())
        if set(node.attrib) - allowed:
            raise ExportError("unsupported source markup attribute")
        if tag == "img":
            source = node.get("src", "")
            if (
                not source.startswith("media/")
                or ".." in Path(source).parts
                or any(char in source for char in (":", "\\", "%", "?", "#"))
            ):
                raise ExportError("image source must be a local bank asset")
        style = node.get("style", "").lower()
        if any(token in style for token in ("url", "expression", "@import", "\\")):
            raise ExportError("unsafe source style")
    return wrapper


def _normalise_model(model, cls):
    try:
        return cls.model_validate(model.model_dump(mode="python"))
    except Exception as exc:
        raise ExportError(f"invalid {cls.__name__} at export boundary") from exc


def _validate_source_bank(
    bank_dir: Path, bank: PracticeBank, curriculum: Curriculum
) -> dict[str, Path]:
    root = bank_dir.resolve(strict=True)
    bank_json = _contained(root, "bank.json")
    raw_bytes = bank_json.read_bytes()
    if _sha256(raw_bytes) != bank.source_bank_sha256:
        raise ExportError("source bank metadata changed since practice-bank construction")
    try:
        raw_bank = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("source bank JSON is unreadable") from exc
    raw_questions = raw_bank.get("questions") if isinstance(raw_bank, dict) else None
    raw_documents = raw_bank.get("documents") if isinstance(raw_bank, dict) else None
    if not isinstance(raw_questions, list) or not isinstance(raw_documents, list):
        raise ExportError("source bank question records are missing")
    raw_by_id = {
        row.get("id"): row
        for row in raw_questions
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if len(raw_by_id) != len(raw_questions) or set(raw_by_id) != {
        question.id for question in bank.questions
    }:
        raise ExportError("practice bank cohort differs from the frozen source bank")
    raw_docs_by_id = {
        row.get("id"): row
        for row in raw_documents
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if len(raw_docs_by_id) != len(raw_documents):
        raise ExportError("source bank document records are invalid")
    for question in bank.questions:
        raw_question = raw_by_id[question.id]
        if canonical_hash(raw_question) != question.source_hash:
            raise ExportError("practice source record differs from the frozen raw bank row")
        if canonical_hash(question.source_payload) != canonical_hash(raw_question):
            raise ExportError("practice source payload was changed after bank construction")
    if bank.curriculum_version != curriculum.version:
        raise ExportError("source bank and curriculum version differ")
    result: dict[str, Path] = {}
    seen_docs: set[str] = set()
    media_bindings: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for question in bank.questions:
        for document in question.documents:
            raw_document = raw_docs_by_id.get(document.id)
            if raw_document is None or raw_document.get("sha256") != document.sha256:
                raise ExportError("source document metadata differs from raw bank")
            copies = raw_document.get("source_copies")
            if not isinstance(copies, list) or not any(
                isinstance(copy_record, dict)
                and copy_record.get("path") == document.path
                and copy_record.get("sha256", document.sha256) == document.sha256
                for copy_record in copies
            ):
                raise ExportError("source document path is not present in raw bank metadata")
            if document.id in seen_docs:
                if result[document.id] != _contained(root, document.path):
                    raise ExportError("duplicate document identity has conflicting paths")
                continue
            seen_docs.add(document.id)
            path = _contained(root, document.path)
            if _file_sha256(path) != document.sha256:
                raise ExportError("source DOCX digest differs from bank metadata")
            result[document.id] = path
        for relative, expected in question.asset_hashes.items():
            asset = _contained(root, relative)
            if _file_sha256(asset) != expected:
                raise ExportError("source media digest differs from bank metadata")
        for block_name in (
            "stem_blocks",
            "analysis_stem_blocks",
            "answer_blocks",
            "analysis_blocks",
        ):
            for block in question.source_payload.get(block_name, []):
                if not isinstance(block, dict):
                    continue
                document_id = block.get("document_id")
                for occurrence in block.get("media_occurrences", []):
                    if not isinstance(occurrence, dict) or occurrence.get("rendered") is not True:
                        continue
                    relative = occurrence.get("media_path")
                    source_part = occurrence.get("source_part")
                    if not isinstance(relative, str) or not isinstance(source_part, str):
                        raise ExportError("source image lacks a document part binding")
                    media_bindings[relative].append((str(document_id), source_part))
    expected_assets = {
        key: value for question in bank.questions for key, value in question.asset_hashes.items()
    }
    for relative, expected in expected_assets.items():
        bindings = media_bindings.get(relative, [])
        if not bindings:
            raise ExportError("source image asset has no source document binding")
        asset_bytes = _contained(root, relative).read_bytes()
        for document_id, source_part in bindings:
            source_doc = result.get(document_id)
            if source_doc is None:
                raise ExportError("source image document is not part of the verified bank")
            try:
                with zipfile.ZipFile(source_doc) as archive:
                    if source_part not in archive.namelist():
                        raise ExportError("source image package part is missing")
                    source_bytes = archive.read(source_part)
            except (OSError, zipfile.BadZipFile) as exc:
                raise ExportError("source image document is unreadable") from exc
            if source_bytes != asset_bytes or _sha256(source_bytes) != expected:
                raise ExportError("source image bytes differ from original DOCX media")
    return result


def _validate_snapshot_membership(snapshot: BankPracticeSnapshot, bank: PracticeBank) -> None:
    if snapshot.bank_sha256 != canonical_hash(bank.model_dump(mode="json")):
        raise ExportError("snapshot refers to a different practice bank")
    by_id = {question.id: question for question in bank.questions}
    if len(by_id) != len(bank.questions):
        raise ExportError("practice bank has duplicate question IDs")
    for item in snapshot.items:
        stored = by_id.get(item.question.id)
        if stored is None or stored.model_dump(mode="json") != item.question.model_dump(
            mode="json"
        ):
            raise ExportError("snapshot question differs from the verified source bank")


def _new_marker(counter: list[int], kind: str, metadata: dict[str, Any]) -> str:
    counter[0] += 1
    token = f"{_MARKER}_{kind}_{counter[0]:08d}"
    metadata["token"] = token
    return token


class _DocumentRenderer:
    def __init__(self, document, bank_root: Path, marker_counter: list[int], metadata: list[dict]):
        self.document = document
        self.bank_root = bank_root
        self.marker_counter = marker_counter
        self.metadata = metadata
        self.image_cache: dict[Path, Path] = {}

    def _add_text(
        self,
        paragraph,
        value: str,
        *,
        context=None,
        bold=False,
        italic=False,
        subscript=False,
        superscript=False,
    ):
        if not value:
            return
        if context and context.get("option_columns"):
            expected_index = context["option_next"]
            if expected_index == 0:
                match = _OPTION_LABEL_RE.match(value)
                if match and match.group(1) == "A":
                    value = value.lstrip()
                    context["option_next"] = 1
            elif expected_index < 4:
                expected_label = "ABCD"[expected_index]
                pattern = re.compile(rf"^\s*{expected_label}[.．、)]\s*")
                match = pattern.match(value)
                if match:
                    separator = expected_index % context["option_columns"]
                    if expected_index and separator == 0:
                        paragraph.add_run().add_break()
                    elif expected_index:
                        paragraph.add_run().add_tab()
                    value = f"{expected_label}. " + value[match.end() :]
                    context["option_next"] += 1
        run = paragraph.add_run(value)
        run.bold = bold
        run.italic = italic
        run.font.subscript = subscript
        run.font.superscript = superscript

    def _occurrence_index(
        self, occurrence_list: list[dict], predicate, used: set[int]
    ) -> int | None:
        for index, occurrence in enumerate(occurrence_list):
            if index not in used and predicate(occurrence):
                used.add(index)
                return index
        return None

    def _render_inline(self, paragraph, node, block, context, *, emphasis=None):
        if node.text:
            self._add_text(paragraph, node.text, context=context, **(emphasis or {}))
        for child in node:
            tag = str(child.tag).lower()
            child_emphasis = dict(emphasis or {})
            if tag in {"b", "strong"}:
                child_emphasis["bold"] = True
            elif tag in {"i", "em"}:
                child_emphasis["italic"] = True
            elif tag == "sub":
                child_emphasis["subscript"] = True
            elif tag == "sup":
                child_emphasis["superscript"] = True
            if tag == "br":
                paragraph.add_run().add_break()
            elif tag == "img":
                self._render_image(paragraph, child, block, context)
            elif tag == "math" or (
                tag == "span" and "unsupported-omml" in child.get("class", "").split()
            ):
                occurrence_index = self._occurrence_index(
                    block.get("omml_occurrences", []), lambda _o: True, context["used_omml"]
                )
                if occurrence_index is None:
                    raise ExportError("MathML has no bound source OMML record")
                occurrence = block["omml_occurrences"][occurrence_index]
                source_xml = occurrence.get("source_xml")
                if not isinstance(source_xml, str) or not source_xml.strip():
                    raise ExportError("source OMML XML is missing")
                marker = {
                    "kind": "omml",
                    "source_xml": source_xml,
                    "source_ref": self._source_ref(block, occurrence, context),
                }
                token = _new_marker(self.marker_counter, "OMML", marker)
                paragraph.add_run(token)
                self.metadata.append(marker)
                context["refs"].append(
                    {"kind": "omml", **self._source_ref(block, occurrence, context)}
                )
            elif tag in {
                "p",
                "span",
                "strong",
                "em",
                "b",
                "i",
                "sub",
                "sup",
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
            }:
                self._render_inline(paragraph, child, block, context, emphasis=child_emphasis)
            elif tag in _ALLOWED_HTML or tag in _MATHML:
                raise ExportError("nested table markup is not supported")
            else:
                raise ExportError("unsupported source markup")
            if child.tail:
                self._add_text(paragraph, child.tail, context=context, **(emphasis or {}))

    @staticmethod
    def _source_ref(block, occurrence, context):
        return {
            "document_id": block.get("document_id"),
            "paragraph_index": occurrence.get("paragraph_index", block.get("paragraph_index")),
            "source_text_range": block.get("source_text_range"),
            "segment_index": block.get("segment_index"),
            "occurrence": occurrence.get("occurrence"),
        }

    def _render_image(self, paragraph, node, block, context):
        relative = node.get("src", "")
        media_occurrences = block.get("media_occurrences", [])
        media_index = self._occurrence_index(
            media_occurrences,
            lambda item: item.get("media_path") == relative,
            context["used_media"],
        )
        if media_index is None:
            raise ExportError("image tag has no bound media occurrence")
        occurrence = media_occurrences[media_index]
        ref = self._source_ref(block, occurrence, context)
        if occurrence.get("kind") == "ole_preview":
            ole_id = occurrence.get("ole_id")
            oles = block.get("ole_occurrences", [])
            matches = [ole for ole in oles if ole.get("ole_id") == ole_id]
            if not ole_id or len(matches) != 1:
                raise ExportError("MathType preview has no unique OLE binding")
            ole = matches[0]
            if occurrence.get("source_rel_id") not in ole.get("preview_rel_ids", []):
                raise ExportError("MathType preview relationship is not bound to its OLE object")
            if relative not in ole.get("preview_media_paths", []):
                raise ExportError("MathType preview path is not bound to its OLE object")
            native = ole.get("native_equation") or {}
            if (
                native.get("status") != "valid"
                or native.get("method") != "native_mtef_exact_v1"
                or native.get("mtef_version") != 5
                or type(native.get("payload_size")) is not int
                or native.get("payload_size", 0) < 1
                or not _SHA256_RE.fullmatch(str(native.get("ole_sha256", "")))
                or not _SHA256_RE.fullmatch(str(native.get("payload_sha256", "")))
            ):
                raise ExportError("MathType native equation is not verifiably available")
            marker = {
                "kind": "ole",
                "document_id": block.get("document_id"),
                "paragraph_index": ref["paragraph_index"],
                "ole_id": ole_id,
                "ole_rel_id": ole.get("ole_rel_id"),
                "preview_rel_id": occurrence.get("source_rel_id"),
                "preview_source_part": occurrence.get("source_part"),
                "source_ref": ref,
                "ole_sha256": native.get("ole_sha256"),
            }
            token = _new_marker(self.marker_counter, "OLE", marker)
            paragraph.add_run(token)
            self.metadata.append(marker)
            context["refs"].append(
                {
                    "kind": "ole",
                    "ole_id": ole_id,
                    **ref,
                    "payload_sha256": native.get("payload_sha256"),
                    "ole_sha256": native.get("ole_sha256"),
                }
            )
            return
        if occurrence.get("rendered") is not True:
            raise ExportError("source image was not rendered by the importer")
        path = _contained(self.bank_root, relative)
        expected = self._expected_asset_hash(relative, context)
        if _file_sha256(path) != expected:
            raise ExportError("image bytes differ from the frozen source asset digest")
        with Image.open(path) as image:
            image.verify()
            pixel_width, pixel_height = image.size
        if pixel_width <= 0 or pixel_height <= 0:
            raise ExportError("source image has invalid dimensions")
        try:
            display_width = float(node.get("width")) if node.get("width") else pixel_width
            display_height = float(node.get("height")) if node.get("height") else pixel_height
        except ValueError as exc:
            raise ExportError("source image dimensions are invalid") from exc
        if display_width <= 0 or display_height <= 0:
            raise ExportError("source image dimensions are invalid")
        # Preserve source display dimensions and aspect ratio, constrained to printable page.
        scale = min(1.0, 6.8 * 96 / display_width, 9.2 * 96 / display_height)
        paragraph.add_run().add_picture(
            str(path),
            width=Inches(display_width * scale / 96),
            height=Inches(display_height * scale / 96),
        )
        context["refs"].append({"kind": "image", **ref, "media_path": relative, "sha256": expected})
        context["counts"]["image_count"] += 1

    @staticmethod
    def _expected_asset_hash(relative: str, context) -> str:
        expected = context["assets"].get(relative)
        if not expected:
            raise ExportError("image has no frozen asset digest")
        return expected

    def _render_table(self, table_node, block, context):
        rows = table_node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
        if not rows:
            raise ExportError("source table contains no rows")
        parsed_rows = []
        width = 0
        for row in rows:
            cells = row.xpath("./td|./th")
            if not cells:
                raise ExportError("source table row contains no cells")
            parsed_rows.append(cells)
            width = max(width, sum(_positive_span(cell.get("colspan")) for cell in cells))
        table = self.document.add_table(rows=len(parsed_rows), cols=width)
        table.style = "Table Grid"
        for row in table.rows:
            row_pr = row._tr.get_or_add_trPr()
            if row_pr.find(f"{{{W}}}cantSplit") is None:
                etree.SubElement(row_pr, f"{{{W}}}cantSplit")
        context["counts"]["table_count"] += 1
        merged_covered: set[tuple[int, int]] = set()
        for row_index, cells in enumerate(parsed_rows):
            col_index = 0
            for cell_node in cells:
                while col_index < width and (row_index, col_index) in merged_covered:
                    col_index += 1
                col_span = _positive_span(cell_node.get("colspan"))
                row_span = _positive_span(cell_node.get("rowspan"))
                if row_index + row_span > len(parsed_rows) or col_index + col_span > width:
                    raise ExportError("source table span exceeds table bounds")
                cell = table.cell(row_index, col_index)
                if col_span > 1 or row_span > 1:
                    cell = cell.merge(
                        table.cell(row_index + row_span - 1, col_index + col_span - 1)
                    )
                    for rr in range(row_index, row_index + row_span):
                        for cc in range(col_index, col_index + col_span):
                            if (rr, cc) != (row_index, col_index):
                                merged_covered.add((rr, cc))
                p = cell.paragraphs[0]
                self._render_inline(p, cell_node, block, context)
                col_index += col_span
        style_source_table(table)
        return table

    def _new_block_context(self, block, assets):
        return {
            "used_media": set(),
            "used_omml": set(),
            "refs": [
                {
                    "kind": "source_block",
                    "document_id": block.get("document_id"),
                    "paragraph_index": block.get("paragraph_index"),
                    "source_text_range": block.get("source_text_range"),
                    "segment_index": block.get("segment_index"),
                }
            ],
            "assets": assets,
            "counts": defaultdict(int),
            "block": block,
        }

    @staticmethod
    def _finish_block(block, context):
        for index, _occurrence in enumerate(block.get("media_occurrences", [])):
            if index not in context["used_media"]:
                raise ExportError("source media occurrence is missing from its HTML fragment")
        for index, _occurrence in enumerate(block.get("omml_occurrences", [])):
            if index not in context["used_omml"]:
                raise ExportError("source OMML occurrence is missing from its HTML fragment")
        return context

    def render_block(
        self,
        block,
        *,
        assets,
        remove_source_number=False,
        source_number=None,
        question_number=None,
        teacher_labels: bool = False,
        body_style: str = "ExamBody",
        option_columns: int | None = None,
        label_prefix: str | None = None,
    ):
        if not isinstance(block, dict) or not isinstance(block.get("html"), str):
            raise ExportError("source block HTML is missing")
        root = _safe_html(block["html"])
        context = self._new_block_context(block, assets)
        if option_columns:
            context["option_columns"] = option_columns
            context["option_next"] = 0
        children = list(root)
        if not children and (root.text or ""):
            p = self.document.add_paragraph(
                style="ExamQuestion"
                if question_number
                else "ExamOption"
                if option_columns
                else body_style
            )
            if option_columns:
                for stop in range(1, option_columns):
                    p.paragraph_format.tab_stops.add_tab_stop(
                        Mm(174 * stop / option_columns), WD_TAB_ALIGNMENT.LEFT
                    )
            text = root.text or ""
            if remove_source_number:
                text = _strip_source_number(text, source_number)
                remove_source_number = False
            if teacher_labels:
                text = _TEACHER_LABEL_RE.sub("", text, count=1)
            if question_number is not None:
                p.paragraph_format.tab_stops.add_tab_stop(Mm(8), WD_TAB_ALIGNMENT.LEFT)
                p.add_run(f"{question_number}.\t")
                context["question_prefix_added"] = True
            if label_prefix:
                p.add_run(f"{label_prefix} ")
            self._add_text(p, text, context=context)
        for child in children:
            tag = str(child.tag).lower()
            if tag == "table":
                if question_number is not None:
                    p = self.document.add_paragraph(style="ExamQuestion")
                    p.paragraph_format.tab_stops.add_tab_stop(Mm(8), WD_TAB_ALIGNMENT.LEFT)
                    p.add_run(f"{question_number}.\t")
                    p.paragraph_format.keep_with_next = True
                    context["question_prefix_added"] = True
                    question_number = None
                self._render_table(child, block, context)
            elif tag in {"p", "div"}:
                p = self.document.add_paragraph(
                    style="ExamQuestion"
                    if question_number is not None
                    else "ExamOption"
                    if option_columns
                    else body_style
                )
                if option_columns:
                    for stop in range(1, option_columns):
                        p.paragraph_format.tab_stops.add_tab_stop(
                            Mm(174 * stop / option_columns), WD_TAB_ALIGNMENT.LEFT
                        )
                if remove_source_number:
                    if source_number and source_number.isdigit():
                        _strip_element_prefix(
                            child,
                            re.compile(rf"^\s*{re.escape(source_number)}\s*[.．、)](?!\d)\s*"),
                        )
                    remove_source_number = False
                if teacher_labels:
                    _strip_element_prefix(child, _TEACHER_LABEL_RE)
                if question_number is not None:
                    p.paragraph_format.tab_stops.add_tab_stop(Mm(8), WD_TAB_ALIGNMENT.LEFT)
                    p.add_run(f"{question_number}.\t")
                    context["question_prefix_added"] = True
                    question_number = None
                if label_prefix:
                    p.add_run(f"{label_prefix} ")
                    label_prefix = None
                self._render_inline(p, child, block, context)
            else:
                p = self.document.add_paragraph(
                    style="ExamQuestion"
                    if question_number is not None
                    else "ExamOption"
                    if option_columns
                    else body_style
                )
                if option_columns:
                    for stop in range(1, option_columns):
                        p.paragraph_format.tab_stops.add_tab_stop(
                            Mm(174 * stop / option_columns), WD_TAB_ALIGNMENT.LEFT
                        )
                if remove_source_number:
                    if source_number and source_number.isdigit():
                        _strip_element_prefix(
                            child,
                            re.compile(rf"^\s*{re.escape(source_number)}\s*[.．、)](?!\d)\s*"),
                        )
                    remove_source_number = False
                if teacher_labels:
                    _strip_element_prefix(child, _TEACHER_LABEL_RE)
                if question_number is not None:
                    p.paragraph_format.tab_stops.add_tab_stop(Mm(8), WD_TAB_ALIGNMENT.LEFT)
                    p.add_run(f"{question_number}.\t")
                    context["question_prefix_added"] = True
                    question_number = None
                if label_prefix:
                    p.add_run(f"{label_prefix} ")
                    label_prefix = None
                self._render_inline(p, child, block, context)
            if child.tail:
                self._add_text(self.document.add_paragraph(), child.tail)
        return self._finish_block(block, context)

    def render_option_block(self, block, paragraph, *, assets, label: str):
        root = _safe_html(block.get("html", ""))
        children = list(root)
        if len(children) != 1 or str(children[0].tag).lower() != "p":
            raise ExportError("choice option fragment is not a single paragraph")
        context = self._new_block_context(block, assets)
        option = children[0]
        _strip_element_prefix(option, _OPTION_LABEL_RE)
        paragraph.add_run(f"{label}. ")
        self._render_inline(paragraph, option, block, context)
        return self._finish_block(block, context)

    def render_option_group(
        self, options, *, assets, columns: int, question_number: int | None = None
    ):
        refs = []
        counts: dict[str, int] = defaultdict(int)
        prefix_added = question_number is not None
        for row_start in range(0, len(options), columns):
            row_options = options[row_start : row_start + columns]
            paragraph = self.document.add_paragraph(style="ExamOption")
            if columns > 1:
                for stop in range(1, columns):
                    paragraph.paragraph_format.tab_stops.add_tab_stop(
                        Mm(174 * stop / columns), WD_TAB_ALIGNMENT.LEFT
                    )
            if question_number is not None:
                paragraph.style = self.document.styles["ExamQuestion"]
                paragraph.paragraph_format.tab_stops.add_tab_stop(Mm(8), WD_TAB_ALIGNMENT.LEFT)
                paragraph.add_run(f"{question_number}.\t")
                question_number = None
            for offset, block in enumerate(row_options):
                label = _option_label(block)
                if label not in "ABCD":
                    raise ExportError("choice option labels are no longer reliable")
                context = self.render_option_block(block, paragraph, assets=assets, label=label)
                refs.extend(context["refs"])
                for key, value in context["counts"].items():
                    counts[key] += value
                if offset < len(row_options) - 1:
                    paragraph.add_run("\t")
        return {"refs": refs, "counts": counts, "question_prefix_added": prefix_added}


def _strip_source_number(value: str, number: str | None) -> str:
    if not number or not number.isdigit():
        return value
    pattern = re.compile(rf"^\s*{re.escape(number)}\s*[.．、)](?!\d)\s*")
    return pattern.sub("", value, count=1)


def _positive_span(value: str | None) -> int:
    if value is None:
        return 1
    if not value.isdigit() or not 1 <= int(value) <= 64:
        raise ExportError("source table has an invalid cell span")
    return int(value)


def _strip_element_prefix(element, pattern: re.Pattern[str]) -> bool:
    """Remove one anchored prefix from the first visible text node only."""
    for event, node in etree.iterwalk(element, events=("start", "end")):
        if event == "start":
            if node is not element and str(node.tag).lower() in {
                "img",
                "math",
                "br",
                "table",
            }:
                return False
            candidate = node.text
        else:
            if node is element:
                continue
            candidate = node.tail
        if candidate and candidate.strip():
            replaced = pattern.sub("", candidate, count=1)
            if replaced != candidate:
                if event == "start":
                    node.text = replaced
                else:
                    node.tail = replaced
                return True
            return False
    return False


def _option_label(block: dict) -> str | None:
    if not isinstance(block, dict) or not isinstance(block.get("text"), str):
        return None
    match = _OPTION_LABEL_RE.match(block["text"])
    return match.group(1) if match else None


def _option_width(block: dict) -> float:
    text = _OPTION_LABEL_RE.sub("", str(block.get("text", "")), count=1)
    width = sum(
        2.0 if unicodedata.east_asian_width(char) in {"F", "W"} else 1.0
        for char in text
        if not char.isspace()
    )
    root = _safe_html(block.get("html", ""))
    for image in root.xpath(".//img"):
        try:
            width += float(image.get("width", "0")) / 5.5
        except ValueError:
            return float("inf")
    # A formula represented as OMML contributes visible width even when source text
    # contains no LaTeX-like characters.
    width += 4.0 * len(block.get("omml_occurrences", []))
    return width


def _option_columns(options: list[dict]) -> int:
    widest = max((_option_width(block) for block in options), default=float("inf"))
    if widest <= 16:
        return 4
    if widest <= 34:
        return 2
    return 1


def _inline_option_labels(block: dict) -> list[re.Match[str]]:
    text = str(block.get("text", ""))
    matches = list(re.finditer(r"(?<![A-Za-z0-9])([A-D])[.．、)](?=\s)", text))
    if [match.group(1) for match in matches] != list("ABCD"):
        return []
    if text[: matches[0].start()].strip():
        return []
    empty_intervals = [
        not text[left.end() : right.start()].strip() for left, right in zip(matches, matches[1:])
    ]
    if any(empty_intervals):
        fragment = _safe_html(block.get("html", ""))
        inline_formula_count = len(block.get("omml_occurrences", []))
        visual_count = len(fragment.xpath(".//img")) + inline_formula_count
        if visual_count < 4:
            return []
    return matches


def _inline_option_columns(block: dict) -> int:
    matches = _inline_option_labels(block)
    if len(matches) != 4:
        return 1
    text = str(block.get("text", ""))
    widths = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index < 3 else len(text)
        segment = text[match.end() : end]
        widths.append(
            sum(
                2.0 if unicodedata.east_asian_width(char) in {"F", "W"} else 1.0
                for char in segment
                if not char.isspace()
            )
        )
    images = _safe_html(block.get("html", "")).xpath(".//img")
    image_width = 0.0
    for image in images:
        try:
            image_width = max(image_width, float(image.get("width", "0")) / 5.5)
        except ValueError:
            return 1
    widths = [width + image_width for width in widths]
    widest = max(widths, default=float("inf"))
    return 4 if widest <= 16 else 2 if widest <= 34 else 1


def _question_units(blocks: list[dict], question_type: str) -> list[tuple[str, Any]]:
    """Return reliable consecutive A-D blocks as option groups; leave all else ordered."""
    if question_type not in {"single_choice", "multiple_choice"}:
        return [("block", block) for block in blocks]
    units: list[tuple[str, Any]] = []
    index = 0
    while index < len(blocks):
        inline_labels = _inline_option_labels(blocks[index])
        if inline_labels:
            root = _safe_html(blocks[index].get("html", ""))
            children = list(root)
            if len(children) == 1 and str(children[0].tag).lower() == "p":
                units.append(("inline_options", blocks[index]))
                index += 1
                continue
        candidate = blocks[index : index + 4]
        if len(candidate) == 4 and [_option_label(block) for block in candidate] == list("ABCD"):
            safe = True
            for block in candidate:
                root = _safe_html(block.get("html", ""))
                children = list(root)
                if len(children) != 1 or str(children[0].tag).lower() != "p":
                    safe = False
                    break
            if safe:
                units.append(("options", candidate))
                index += 4
                continue
        units.append(("block", blocks[index]))
        index += 1
    return units


def _teacher_block_has_content(block: dict) -> bool:
    text = str(block.get("text", ""))
    text = _TEACHER_LABEL_RE.sub("", text, count=1).strip()
    if text:
        return True
    return bool(
        block.get("media_occurrences")
        or block.get("omml_occurrences")
        or block.get("ole_occurrences")
    )


def _set_xml_keep_next(paragraph_element, enabled: bool) -> None:
    p_pr = paragraph_element.find(f"{{{W}}}pPr")
    if p_pr is None:
        p_pr = etree.Element(f"{{{W}}}pPr")
        paragraph_element.insert(0, p_pr)
    keep_next = p_pr.find(f"{{{W}}}keepNext")
    if keep_next is None:
        keep_next = etree.Element(f"{{{W}}}keepNext")
        style = p_pr.find(f"{{{W}}}pStyle")
        p_pr.insert(p_pr.index(style) + 1 if style is not None else 0, keep_next)
    if enabled:
        keep_next.attrib.pop(f"{{{W}}}val", None)
    else:
        keep_next.set(f"{{{W}}}val", "0")


def _set_xml_keep_lines(paragraph_element) -> None:
    p_pr = paragraph_element.find(f"{{{W}}}pPr")
    if p_pr is None:
        p_pr = etree.Element(f"{{{W}}}pPr")
        paragraph_element.insert(0, p_pr)
    if p_pr.find(f"{{{W}}}keepLines") is None:
        keep_lines = etree.Element(f"{{{W}}}keepLines")
        keep_next = p_pr.find(f"{{{W}}}keepNext")
        style = p_pr.find(f"{{{W}}}pStyle")
        anchor = keep_next if keep_next is not None else style
        p_pr.insert(p_pr.index(anchor) + 1 if anchor is not None else 0, keep_lines)


def _keep_short_question_within_its_stem(stem_children) -> None:
    """Keep a short stem together without chaining its last paragraph to the next item."""
    for index, element in enumerate(stem_children):
        if element.tag == W_P:
            _set_xml_keep_lines(element)
            _set_xml_keep_next(element, index < len(stem_children) - 1)
        elif element.tag == f"{{{W}}}tbl":
            for row in element.iter(f"{{{W}}}tr"):
                row_pr = row.find(f"{{{W}}}trPr")
                if row_pr is None:
                    row_pr = etree.Element(f"{{{W}}}trPr")
                    row.insert(0, row_pr)
                if row_pr.find(f"{{{W}}}cantSplit") is None:
                    etree.SubElement(row_pr, f"{{{W}}}cantSplit")
            table_paragraphs = list(element.iter(W_P))
            follows_stem_content = any(
                child.tag in {W_P, f"{{{W}}}tbl"} for child in stem_children[index + 1 :]
            )
            for paragraph_index, paragraph in enumerate(table_paragraphs):
                _set_xml_keep_lines(paragraph)
                keep_with_next = paragraph_index < len(table_paragraphs) - 1 or follows_stem_content
                _set_xml_keep_next(paragraph, keep_with_next)


def _subquestion_count(blocks: list[dict]) -> int:
    labels = set()
    for block in blocks:
        text = str(block.get("text", ""))
        for line in text.splitlines():
            match = re.match(r"^\s*[（(](\d{1,2})[）)]", line)
            if match:
                labels.add(match.group(1))
    return max(1, len(labels))


def _group_name(curriculum: Curriculum, point_id: str) -> str:
    matches = [point.name for point in curriculum.points if point.id == point_id]
    if len(matches) != 1:
        raise ExportError("practice target point is missing from curriculum")
    return matches[0]


def _document_for_item(
    item,
    *,
    teacher: bool,
    layout: LayoutMode,
    curriculum: Curriculum,
    bank_root: Path,
    marker_counter: list[int],
    marker_metadata: list[dict],
    assets: dict[str, str],
):
    document = make_exam_document(
        teacher=teacher,
        layout=layout,
        is_synthetic_student=item.snapshot_is_synthetic,
    )
    renderer = _DocumentRenderer(document, bank_root, marker_counter, marker_metadata)
    counts: dict[str, int] = defaultdict(int)
    refs: list[dict] = []
    active_point = None
    for practice_item in item.snapshot_items:
        if practice_item.target_point_id != active_point:
            document.add_paragraph(
                _group_name(curriculum, practice_item.target_point_id), style="ExamSection"
            )
            active_point = practice_item.target_point_id
        question = practice_item.question
        payload = question.source_payload
        question_type = str(payload.get("question_type", ""))
        raw_number = payload.get("number")
        source_number = str(raw_number) if isinstance(raw_number, (str, int)) else None
        if teacher:
            answer_blocks = [
                block
                for block in payload.get("answer_blocks", [])
                if _teacher_block_has_content(block)
            ]
            analysis_blocks = [
                block
                for block in payload.get("analysis_blocks", [])
                if _teacher_block_has_content(block)
            ]
            prefix_pending = True
            if answer_blocks:
                for block_index, block in enumerate(answer_blocks):
                    context = renderer.render_block(
                        block,
                        assets=assets,
                        teacher_labels=True,
                        body_style="ExamAnswer",
                        question_number=practice_item.order if prefix_pending else None,
                        label_prefix="答案：" if block_index == 0 else None,
                    )
                    prefix_pending = False
                    refs.extend(context["refs"])
                    for key, value in context["counts"].items():
                        counts[key] += value
            if analysis_blocks:
                for block_index, block in enumerate(analysis_blocks):
                    context = renderer.render_block(
                        block,
                        assets=assets,
                        teacher_labels=True,
                        body_style="ExamAnswer",
                        question_number=practice_item.order if prefix_pending else None,
                        label_prefix="解析：" if block_index == 0 else None,
                    )
                    prefix_pending = False
                    refs.extend(context["refs"])
                    for key, value in context["counts"].items():
                        counts[key] += value
            continue

        stem_blocks = payload.get("stem_blocks", [])
        question_units = _question_units(stem_blocks, question_type)
        body_children = [child for child in document._body._body if child.tag != f"{{{W}}}sectPr"]
        stem_start = len(body_children)
        prefix_added = False
        first_stem_block = True
        for unit_kind, unit in question_units:
            if unit_kind == "options":
                columns = _option_columns(unit)
                context = renderer.render_option_group(
                    unit,
                    assets=assets,
                    columns=columns,
                    question_number=None if prefix_added else practice_item.order,
                )
                prefix_added = prefix_added or context["question_prefix_added"]
            elif unit_kind == "inline_options":
                context = renderer.render_block(
                    unit,
                    assets=assets,
                    remove_source_number=first_stem_block,
                    source_number=source_number,
                    question_number=None if prefix_added else practice_item.order,
                    body_style="ExamOption",
                    option_columns=_inline_option_columns(unit),
                )
                prefix_added = prefix_added or context.get("question_prefix_added", False)
            else:
                context = renderer.render_block(
                    unit,
                    assets=assets,
                    remove_source_number=first_stem_block,
                    source_number=source_number,
                    question_number=None if prefix_added else practice_item.order,
                    body_style="ExamBody",
                )
                prefix_added = prefix_added or context.get("question_prefix_added", False)
            first_stem_block = False
            refs.extend(context["refs"])
            for key, value in context["counts"].items():
                counts[key] += value
        if not prefix_added:
            question_heading = document.add_paragraph(style="ExamQuestion")
            question_heading.paragraph_format.tab_stops.add_tab_stop(Mm(8), WD_TAB_ALIGNMENT.LEFT)
            question_heading.add_run(f"{practice_item.order}.\t")
            question_heading.paragraph_format.keep_with_next = True
        if question_type in {
            "single_choice",
            "multiple_choice",
            "fill_blank",
            "单选",
            "填空",
            "选择题",
        }:
            stem_children = [
                child for child in document._body._body if child.tag != f"{{{W}}}sectPr"
            ][stem_start:]
            _keep_short_question_within_its_stem(stem_children)
        append_answer_space(
            document,
            question_type=question_type,
            layout=layout,
            subquestion_count=_subquestion_count(stem_blocks),
        )
    return document, refs, counts


def _extract_source_ole(
    document_path: Path, paragraph_index: int, ole_id: str, ole_rel_id: str | None
):
    if not ole_id and not ole_rel_id:
        raise ExportError("source OLE reference has no stable identity")
    try:
        archive = zipfile.ZipFile(document_path)
        doc_root = etree.fromstring(archive.read("word/document.xml"))
        rel_root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    except (OSError, KeyError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise ExportError("source DOCX package is invalid") from exc
    body = doc_root.find(f".//{{{W}}}body")
    paragraphs = list(body.iter(W_P)) if body is not None else []
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        archive.close()
        raise ExportError("source OLE paragraph reference is out of range")
    paragraph = paragraphs[paragraph_index]
    objects = []
    for obj in paragraph.iter(f"{{{W}}}object"):
        ole_nodes = list(obj.iter(f"{{{OFFICE}}}OLEObject"))
        if any(
            (node.get("ShapeID") == ole_id if ole_id else True)
            and (node.get(f"{{{R}}}id") == ole_rel_id if ole_rel_id else True)
            for node in ole_nodes
        ):
            objects.append(obj)
    if len(objects) != 1:
        archive.close()
        raise ExportError("source OLE object binding is missing or ambiguous")
    rels = {rel.get("Id"): rel for rel in rel_root}
    return archive, deepcopy(objects[0]), rels, doc_root


def _package_member(target: str) -> str:
    if target.startswith("/") or "\\" in target:
        raise ExportError("source DOCX contains an unsafe relationship target")
    member = posixpath.normpath(posixpath.join("word", target))
    if member.startswith("../") or member in {"..", "."}:
        raise ExportError("source DOCX relationship escapes its package")
    return member


def _word_text(elements) -> str:
    parts = []
    for element in elements:
        for node in element.iter():
            if node.tag == W_T and node.text:
                parts.append(node.text)
            elif node.tag == f"{{{W}}}tab":
                parts.append("\t")
            elif node.tag == f"{{{W}}}br":
                parts.append("\n")
    return "".join(parts)


def _large_mathtype_height_pt(object_element) -> float | None:
    math_type_objects = [
        node
        for node in object_element.iter(f"{{{OFFICE}}}OLEObject")
        if any(token in node.get("ProgID", "").lower() for token in ("equation.", "mathtype"))
    ]
    if len(math_type_objects) != 1:
        return None
    shape_id = math_type_objects[0].get("ShapeID")
    shapes = [
        node
        for node in object_element.iter(f"{{{V}}}shape")
        if shape_id and node.get("id") == shape_id
    ]
    if len(shapes) != 1:
        return None
    for declaration in shapes[0].get("style", "").split(";"):
        key, separator, value = declaration.partition(":")
        if not separator or key.strip().lower() != "height":
            continue
        match = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*pt\s*", value, re.I)
        return float(match.group(1)) if match else None
    return None


def _separate_large_display_formula(parent, placeholder_run, output_run, object_element) -> bool:
    """Move one large MathType marker after a short, explicit formula introduction."""
    if parent.tag != W_P:
        return False
    if any(
        parent.find(f".//{{{namespace}}}{local_name}") is not None
        for namespace, local_name in (
            (W, "object"),
            (W, "drawing"),
            (M, "oMath"),
            (M, "oMathPara"),
        )
    ):
        return False
    height = _large_mathtype_height_pt(object_element)
    if height is None or height < 20:
        return False
    token_text = _word_text([placeholder_run])
    if not re.fullmatch(r"TSXMARKER_OLE_\d{8}", token_text):
        return False
    paragraph_tokens = re.findall(r"TSXMARKER_(?:OLE|OMML)_\d{8}", _word_text([parent]))
    if paragraph_tokens != [token_text]:
        return False

    children = list(parent)
    run_index = children.index(placeholder_run)
    before_nodes = [node for node in children[:run_index] if node.tag != f"{{{W}}}pPr"]
    after_nodes = children[run_index + 1 :]
    intro = _word_text(before_nodes)
    trailing = _word_text(after_nodes)
    if not _DISPLAY_FORMULA_INTRO_RE.fullmatch(intro):
        return False
    if trailing.strip(" \t\r\n。；;，,、：:") or any(
        node.tag != W_R or any(child.tag != f"{{{W}}}rPr" and child.tag != W_T for child in node)
        for node in after_nodes
    ):
        return False

    p_pr = parent.find(f"{{{W}}}pPr")
    source_style = p_pr.find(f"{{{W}}}pStyle") if p_pr is not None else None
    style_name = source_style.get(f"{{{W}}}val") if source_style is not None else ""
    display_style = "ExamAnswer" if style_name == "ExamAnswer" else "ExamBody"
    display_paragraph = etree.Element(W_P)
    display_p_pr = etree.SubElement(display_paragraph, f"{{{W}}}pPr")
    display_p_style = etree.SubElement(display_p_pr, f"{{{W}}}pStyle")
    display_p_style.set(f"{{{W}}}val", display_style)
    display_indent = etree.SubElement(display_p_pr, f"{{{W}}}ind")
    display_indent.set(f"{{{W}}}left", "454")
    display_indent.set(f"{{{W}}}firstLine", "0")

    _set_xml_keep_next(parent, True)
    _set_xml_keep_next(display_paragraph, False)
    parent.remove(placeholder_run)
    display_paragraph.append(output_run)
    for trailing_run in after_nodes:
        parent.remove(trailing_run)
        display_paragraph.append(trailing_run)
    parent.addnext(display_paragraph)
    return True


def _remap_docx_markers(docx_path: Path, markers: list[dict], documents: dict[str, Path]) -> None:
    if not markers:
        return
    try:
        with zipfile.ZipFile(docx_path) as source_zip:
            files = {name: source_zip.read(name) for name in source_zip.namelist()}
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExportError("generated DOCX package is unreadable") from exc
    doc_root = etree.fromstring(files["word/document.xml"])
    rel_path = "word/_rels/document.xml.rels"
    rel_root = etree.fromstring(files[rel_path])
    content_types = etree.fromstring(files["[Content_Types].xml"])
    rel_by_id = {rel.get("Id"): rel for rel in rel_root}
    next_rid = (
        max(
            [
                int(match.group(1))
                for rid in rel_by_id
                if (match := re.fullmatch(r"rId(\d+)", rid or ""))
            ]
            + [0]
        )
        + 1
    )
    next_shape = 1024
    next_drawing_id = (
        max(
            [
                int(value)
                for node in doc_root.iter()
                for key, value in node.attrib.items()
                if etree.QName(key).localname == "id" and value.isdigit()
            ]
            + [0]
        )
        + 1
    )
    embedding_index = 0
    media_index = 0
    source_archives: dict[
        str, tuple[zipfile.ZipFile, dict, dict[tuple[int, str], Any], dict[str, Any]]
    ] = {}

    def add_relationship(target: str, rel_type: str) -> str:
        nonlocal next_rid
        rid = f"rId{next_rid}"
        next_rid += 1
        etree.SubElement(rel_root, f"{{{PKG}}}Relationship", Id=rid, Type=rel_type, Target=target)
        return rid

    def add_content_type(part_name: str, content_type: str):
        found = content_types.find(f"{{{CT}}}Override[@PartName='{part_name}']")
        if found is None:
            etree.SubElement(
                content_types, f"{{{CT}}}Override", PartName=part_name, ContentType=content_type
            )

    def copy_target(archive, source_rel, kind):
        nonlocal embedding_index, media_index
        target = source_rel.get("Target", "")
        if source_rel.get("TargetMode") == "External" or not target:
            raise ExportError("source OLE has an external relationship")
        member = _package_member(target)
        if member not in archive.namelist():
            raise ExportError("source OLE relationship target is missing")
        data = archive.read(member)
        if kind == "image":
            suffix = Path(member).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".emf", ".wmf"}:
                raise ExportError("unsupported OLE preview media type")
            media_index += 1
            out_member = f"word/media/source_ole_preview_{media_index:05d}{suffix}"
            content_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".tif": "image/tiff",
                ".tiff": "image/tiff",
                ".emf": "image/x-emf",
                ".wmf": "image/x-wmf",
            }[suffix]
        else:
            embedding_index += 1
            out_member = (
                f"word/embeddings/source_ole_{embedding_index:05d}{Path(member).suffix or '.bin'}"
            )
            content_type = "application/vnd.openxmlformats-officedocument.oleObject"
        files[out_member] = data
        add_content_type("/" + out_member, content_type)
        dest = posixpath.relpath(out_member, "word")
        rid = add_relationship(dest, source_rel.get("Type", ""))
        return rid

    try:
        # Replace each dedicated placeholder run with original OMML or a copied OLE object.
        for marker in markers:
            token = marker["token"]
            runs = [node for node in doc_root.iter(W_R) if "".join(node.itertext()) == token]
            if len(runs) != 1:
                raise ExportError("generated DOCX marker is missing or ambiguous")
            run = runs[0]
            parent = run.getparent()
            position = parent.index(run)
            if marker["kind"] == "omml":
                try:
                    math_node = etree.fromstring(
                        marker["source_xml"].encode("utf-8"),
                        parser=etree.XMLParser(resolve_entities=False, no_network=True),
                    )
                except etree.XMLSyntaxError as exc:
                    raise ExportError("source OMML XML cannot be parsed") from exc
                if math_node.tag not in {f"{{{M}}}oMath", f"{{{M}}}oMathPara"}:
                    raise ExportError("source formula is not a native OMML node")
                parent.remove(run)
                parent.insert(position, math_node)
                continue
            source_doc = documents.get(marker["document_id"])
            if source_doc is None:
                raise ExportError("source document for OLE object is missing")
            cache_key = str(source_doc)
            source_key = (marker["paragraph_index"], marker["ole_id"])
            if cache_key not in source_archives or source_key not in source_archives[cache_key][2]:
                opened, object_element, rels, source_root = _extract_source_ole(
                    source_doc,
                    marker["paragraph_index"],
                    marker["ole_id"],
                    marker.get("ole_rel_id"),
                )
                if cache_key in source_archives:
                    opened.close()
                    archive, cached_rels, object_cache, shape_types = source_archives[cache_key]
                    object_cache[source_key] = object_element
                    source_archives[cache_key] = (archive, cached_rels, object_cache, shape_types)
                else:
                    shape_types = {
                        node.get("id"): deepcopy(node)
                        for node in source_root.iter(f"{{{V}}}shapetype")
                        if node.get("id")
                    }
                    source_archives[cache_key] = (
                        opened,
                        rels,
                        {source_key: object_element},
                        shape_types,
                    )
            archive, rels, object_cache, shape_types = source_archives[cache_key]
            object_element = deepcopy(object_cache[source_key])
            rel_map = {}
            for element in object_element.iter():
                for attr in (f"{{{R}}}id", f"{{{R}}}embed", f"{{{R}}}link"):
                    old = element.get(attr)
                    if old is None:
                        continue
                    if old in rel_map:
                        element.set(attr, rel_map[old])
                        continue
                    source_rel = rels.get(old)
                    if source_rel is None:
                        raise ExportError("source OLE object references a missing relationship")
                    rel_type = source_rel.get("Type", "")
                    if rel_type.endswith("/image"):
                        relation_kind = "image"
                    elif rel_type.endswith(("/oleObject", "/package")):
                        relation_kind = "ole"
                    else:
                        raise ExportError("source OLE object has an unsupported relationship type")
                    if old == marker.get("preview_rel_id"):
                        actual_preview_part = _package_member(source_rel.get("Target", ""))
                        if actual_preview_part != marker.get("preview_source_part"):
                            raise ExportError("source OLE preview relationship target changed")
                    if relation_kind == "ole":
                        source_bytes = archive.read(_package_member(source_rel.get("Target", "")))
                        if (
                            marker.get("ole_sha256")
                            and _sha256(source_bytes) != marker["ole_sha256"]
                        ):
                            raise ExportError("native OLE bytes differ from the frozen digest")
                    new_id = copy_target(archive, source_rel, relation_kind)
                    rel_map[old] = new_id
                    element.set(attr, new_id)
            for element in object_element.iter():
                qname = etree.QName(element)
                if qname.namespace == V and qname.localname == "shape" and "id" in element.attrib:
                    next_shape += 1
                    shape_id = f"_x0000_i{next_shape}"
                    element.set("id", shape_id)
                    for ole_node in object_element.iter(f"{{{OFFICE}}}OLEObject"):
                        ole_node.set("ShapeID", shape_id)
                        object_id = f"_{{{uuid.uuid5(uuid.NAMESPACE_URL, marker['token'])}}}"
                        ole_node.set("ObjectID", object_id)
                    shape_type_ref = element.get("type", "").lstrip("#")
                    if shape_type_ref and not any(
                        node.get("id") == shape_type_ref
                        for node in doc_root.iter(f"{{{V}}}shapetype")
                    ):
                        shape_type = shape_types.get(shape_type_ref)
                        shape_style = element.get(f"{{{OFFICE}}}spt")
                        if shape_type is None and not (
                            shape_type_ref == "_x0000_t75" and shape_style == "75"
                        ):
                            raise ExportError("source OLE refers to a missing VML shape type")
                        if shape_type is not None:
                            pict = element.getparent()
                            pict.insert(pict.index(element), deepcopy(shape_type))
                if qname.namespace == WP and qname.localname == "docPr":
                    element.set("id", str(next_drawing_id))
                    next_drawing_id += 1
                if qname.namespace == PIC and qname.localname == "cNvPr":
                    element.set("id", str(next_drawing_id))
                    next_drawing_id += 1
            new_run = etree.Element(W_R)
            run_properties = run.find(f"{{{W}}}rPr")
            if run_properties is not None:
                new_run.append(deepcopy(run_properties))
            new_run.append(object_element)
            if _separate_large_display_formula(parent, run, new_run, object_element):
                continue
            parent.remove(run)
            parent.insert(position, new_run)
        files["word/document.xml"] = etree.tostring(
            doc_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        files[rel_path] = etree.tostring(
            rel_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        files["[Content_Types].xml"] = etree.tostring(
            content_types, xml_declaration=True, encoding="UTF-8", standalone=True
        )
    finally:
        for archive, _, _, _ in source_archives.values():
            archive.close()
    tmp = docx_path.with_suffix(".rewrite.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as target_zip:
        for name, data in files.items():
            target_zip.writestr(name, data)
    tmp.replace(docx_path)


def _write_one(
    docx_path: Path,
    *,
    snapshot,
    teacher: bool,
    layout: LayoutMode,
    curriculum: Curriculum,
    bank_root: Path,
    documents: dict[str, Path],
    assets: dict[str, str],
):
    marker_counter = [0]
    markers: list[dict] = []
    context = type("Context", (), {})()
    context.snapshot_is_synthetic = snapshot.is_synthetic_student
    context.snapshot_items = snapshot.items
    document, refs, counts = _document_for_item(
        context,
        teacher=teacher,
        layout=layout,
        curriculum=curriculum,
        bank_root=bank_root,
        marker_counter=marker_counter,
        marker_metadata=markers,
        assets=assets,
    )
    document.save(docx_path)
    _remap_docx_markers(docx_path, markers, documents)
    counts_dict = {
        "native_ole_count": sum(marker["kind"] == "ole" for marker in markers),
        "omml_native_count": sum(marker["kind"] == "omml" for marker in markers),
        "image_count": counts.get("image_count", 0),
        "table_count": counts.get("table_count", 0),
        "source_ref_count": len(refs),
        "ole_fallback_count": 0,
    }
    return refs, counts_dict


def _write_manifest(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_bank_snapshot(
    snapshot: BankPracticeSnapshot,
    output_dir: str | Path,
    *,
    bank_dir: str | Path,
    practice_bank: PracticeBank,
    curriculum: Curriculum,
    current_progress: StudentProgress,
    soffice_executable: str | Path | None = None,
    layout: LayoutMode = "answer_space",
) -> dict:
    """Export student/teacher DOCX and PDF files as one verified directory."""
    snapshot = _normalise_model(snapshot, BankPracticeSnapshot)
    if layout not in {"answer_space", "compact"}:
        raise ExportError("unsupported exam layout mode")
    practice_bank = _normalise_model(practice_bank, PracticeBank)
    curriculum = _normalise_model(curriculum, Curriculum)
    current_progress = _normalise_model(current_progress, StudentProgress)
    if not snapshot.items:
        raise ExportError("cannot export an empty source-practice snapshot")
    if snapshot.curriculum_version != curriculum.version:
        raise ExportError("snapshot and curriculum versions differ")
    _validate_snapshot_membership(snapshot, practice_bank)
    try:
        from app.bank_practice import (
            validate_bank_curriculum,
            validate_bank_progress,
            validate_bank_snapshot,
        )

        validate_bank_curriculum(practice_bank, curriculum)
        validate_bank_snapshot(snapshot, practice_bank)
        validate_bank_progress(snapshot, curriculum, current_progress)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExportError("snapshot, curriculum, or progress validation failed") from exc
    documents = _validate_source_bank(Path(bank_dir), practice_bank, curriculum)
    for item in snapshot.items:
        if item.question.status != "eligible":
            raise ExportError("snapshot includes a blocked source question")
        payload = item.question.source_payload
        if (
            payload.get("structure_status") != "imported"
            or payload.get("association_status") != "confirmed"
        ):
            raise ExportError("snapshot source structure or answer association is not confirmed")
        if payload.get("answer_available") is not True:
            raise ExportError("teacher export requires an existing answer")
    soffice = "soffice" if tools_enabled() else (
        _available_executable(soffice_executable)
        if soffice_executable is not None
        else shutil.which("soffice")
    )
    if not soffice:
        raise ConverterUnavailable("LibreOffice soffice is required for PDF export")
    final_dir = Path(output_dir).absolute()
    if final_dir.exists() or final_dir.is_symlink():
        raise ExportError("export destination already exists")
    bank_root = Path(bank_dir).resolve(strict=True)
    try:
        final_dir.resolve(strict=False).relative_to(bank_root)
    except ValueError:
        pass
    else:
        raise ExportError("export destination must be outside the source bank")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{final_dir.name}-", dir=final_dir.parent))
    try:
        student_docx = stage / "student.docx"
        teacher_docx = stage / "teacher.docx"
        student_refs, student_counts = _write_one(
            student_docx,
            snapshot=snapshot,
            teacher=False,
            layout=layout,
            curriculum=curriculum,
            bank_root=Path(bank_dir).resolve(strict=True),
            documents=documents,
            assets={
                key: value
                for question in practice_bank.questions
                for key, value in question.asset_hashes.items()
            },
        )
        teacher_refs, teacher_counts = _write_one(
            teacher_docx,
            snapshot=snapshot,
            teacher=True,
            layout=layout,
            curriculum=curriculum,
            bank_root=Path(bank_dir).resolve(strict=True),
            documents=documents,
            assets={
                key: value
                for question in practice_bank.questions
                for key, value in question.asset_hashes.items()
            },
        )
        student_pdf = _convert_pdf(student_docx, stage, soffice, stage / ".lo-profile-student")
        teacher_pdf = _convert_pdf(teacher_docx, stage, soffice, stage / ".lo-profile-teacher")
        shutil.rmtree(stage / ".lo-profile-student", ignore_errors=True)
        shutil.rmtree(stage / ".lo-profile-teacher", ignore_errors=True)
        files = {
            "student_docx": student_docx,
            "student_pdf": student_pdf,
            "teacher_docx": teacher_docx,
            "teacher_pdf": teacher_pdf,
        }
        manifest = {
            "schema_version": "source-practice-export-v2",
            "layout": layout,
            "template_version": TEMPLATE_VERSION,
            "teacher_content": "answers_and_solutions",
            "snapshot_id": canonical_hash(snapshot.model_dump(mode="json")),
            "source_bank_sha256": practice_bank.source_bank_sha256,
            "annotations_sha256": practice_bank.annotations_sha256,
            "curriculum_sha256": practice_bank.curriculum_sha256,
            "curriculum_version": curriculum.version,
            "current_progress_revision": current_progress.revision,
            "progress_validation": "passed",
            "item_count": len(snapshot.items),
            "is_synthetic_student": snapshot.is_synthetic_student,
            "counts": {"student": student_counts, "teacher": teacher_counts},
            "source_refs": {"student": student_refs, "teacher": teacher_refs},
            "files": {
                name: {"path": path.name, "sha256": _file_sha256(path)}
                for name, path in files.items()
            },
        }
        manifest_path = stage / "manifest.json"
        _write_manifest(manifest_path, manifest)
        # Confirm all four artifacts are intact before the atomic directory rename.
        for path in files.values():
            if not path.is_file() or path.stat().st_size == 0:
                raise ExportError("export artifact is missing or empty")
        stage.rename(final_dir)
        published_files = {name: str(final_dir / path.name) for name, path in files.items()}
        return {
            "snapshot_id": manifest["snapshot_id"],
            "current_progress_revision": current_progress.revision,
            "curriculum_version": curriculum.version,
            "progress_validation": "passed",
            "files": published_files,
            "manifest_path": str(final_dir / "manifest.json"),
            "counts": manifest["counts"],
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
