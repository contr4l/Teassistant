"""Loss-conscious OOXML importer for paired source exam and answer-analysis DOCX.

The importer preserves source DOCX files and the original XML/relationships. It
does not convert OLE equations or claim that imported questions are practice-ready.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.native_equation import extract_native_equation
from app.source_association import compare_association

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W}
W_P, W_T, W_TAB, W_BR = f"{{{W}}}p", f"{{{W}}}t", f"{{{W}}}tab", f"{{{W}}}br"
W_CR, W_OBJECT, W_PICT, W_TBL = f"{{{W}}}cr", f"{{{W}}}object", f"{{{W}}}pict", f"{{{W}}}tbl"
W_TR, W_TC = f"{{{W}}}tr", f"{{{W}}}tc"
R_ID, R_EMBED = f"{{{R}}}id", f"{{{R}}}embed"
QUESTION_START = re.compile(r"^\s*(\d{1,3})\s*[.．、)]\s*(?=\S)")
SECTION_START = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+|\d+)[、.．)]\s*.*?(?:单选|多选|选择|填空|解答|判断|证明)题"
)
SECTION_LABELS = ("单选", "多选", "选择", "填空", "解答", "判断", "证明")
ANSWER_LABELS = {
    "【答案】": "answer",
    "【解析】": "analysis",
    "【分析】": "analysis",
    "【详解】": "analysis",
    "【点睛】": "analysis",
}
LABEL_RE = re.compile("|".join(re.escape(label) for label in ANSWER_LABELS))


class DocxIngestError(RuntimeError):
    """Safe import failure without archive contents or source text in the message."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_pair_name(filename: str) -> tuple[str, str]:
    title = unicodedata.normalize("NFKC", Path(filename).stem).strip()
    title = re.sub(r"^精品解析\s*[:：]\s*", "", title)
    title = re.sub(r"\s*[（(](?:原卷版|解析版|答案版|详解版)[）)]\s*$", "", title)
    title = re.sub(r"\s*(?:原卷版|解析版|答案版|详解版|答案解析|试题解析)\s*$", "", title)
    key = re.sub(r"\s+", "", title)
    return key, title


def _package_member(target: str, source_part: str = "word/document.xml") -> str | None:
    if not target or target.startswith("\\"):
        return None
    if target.startswith("/"):
        member = posixpath.normpath(target.lstrip("/"))
    else:
        member = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
    if member in {".", ".."} or member.startswith("../") or member.startswith("/"):
        return None
    return member


def _dimension_px(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(pt|px|in|cm|mm)?\s*", value, re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    px = amount * {"pt": 96 / 72, "px": 1, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4}[unit]
    return max(1, round(px))


def _vml_size(shape: ET.Element | None) -> tuple[int | None, int | None]:
    if shape is None:
        return None, None
    style = shape.get("style", "")
    values: dict[str, str] = {}
    for part in style.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            values[name.strip().lower()] = value.strip()
    return _dimension_px(values.get("width") or shape.get("width")), _dimension_px(
        values.get("height") or shape.get("height")
    )


def _drawing_size(element: ET.Element) -> tuple[int | None, int | None]:
    extent = next(
        (
            item
            for item in element.iter(
                "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"
            )
        ),
        None,
    )
    if extent is None:
        return None, None
    try:
        cx, cy = int(extent.get("cx", "0")), int(extent.get("cy", "0"))
    except ValueError:
        return None, None
    return (
        max(1, round(cx / 9525)) if cx > 0 else None,
        max(1, round(cy / 9525)) if cy > 0 else None,
    )


@dataclass
class _ElementBlock:
    kind: str
    start_paragraph: int
    end_paragraph: int
    block: dict[str, Any]


@dataclass
class _Document:
    record: dict[str, Any]
    paragraphs: list[dict[str, Any]]
    elements: list[_ElementBlock]
    issues: list[dict[str, Any]]
    section_heads: list[dict[str, Any]]
    first_question_index: int | None = None
    starts: dict[int, int] | None = None
    duplicates: dict[int, list[int]] | None = None
    declared_question_counts: list[dict[str, Any]] | None = None


class _Renderer:
    def __init__(
        self,
        zf: zipfile.ZipFile,
        rels: dict[str, dict[str, str]],
        doc_id: str,
        output_root: Path,
        issues: list[dict[str, Any]],
    ):
        self.zf = zf
        self.rels = rels
        self.doc_id = doc_id
        self.output_root = output_root
        self.issues = issues
        self.part_to_path: dict[str, str] = {}
        self.written: set[str] = set()
        self.occurrence_index = 0
        self.omml_occurrence_index = 0
        self.native_equation_cache: dict[str, dict[str, Any]] = {}

    def _image(
        self,
        rel_id: str | None,
        paragraph_index: int,
        kind: str,
        ole_id: str | None = None,
        size: tuple[int | None, int | None] = (None, None),
    ) -> tuple[str, dict[str, Any] | None]:
        self.occurrence_index += 1
        occurrence: dict[str, Any] = {
            "occurrence": self.occurrence_index,
            "paragraph_index": paragraph_index,
            "source_rel_id": rel_id,
            "kind": kind,
            "ole_id": ole_id,
            "source_part": None,
            "media_path": None,
            "rendered": False,
        }
        rel = self.rels.get(rel_id or "")
        member = (
            None
            if not rel or rel.get("TargetMode") == "External"
            else _package_member(rel.get("Target", ""))
        )
        occurrence["source_part"] = member or (rel.get("Target") if rel else None)
        if (
            member is None
            or not member.startswith("word/media/")
            or member not in self.zf.namelist()
        ):
            self.issues.append(
                {
                    "code": "missing_media_relationship",
                    "paragraph_index": paragraph_index,
                    "source_rel_id": rel_id,
                }
            )
            return "<span>[图片预览缺失]</span>", occurrence
        dest = self.part_to_path.get(member)
        if dest is None:
            raw_name = Path(member).name
            safe_name = re.sub(r"[^\w.()\-\u0080-\uffff]", "_", raw_name, flags=re.UNICODE)
            if not safe_name or safe_name in {".", ".."}:
                safe_name = "image.bin"
            digest = hashlib.sha256(member.encode("utf-8")).hexdigest()[:8]
            if any(path.endswith("/" + safe_name) for path in self.part_to_path.values()):
                safe_name = f"{Path(safe_name).stem}-{digest}{Path(safe_name).suffix}"
            dest = f"media/{self.doc_id}/{safe_name}"
            self.part_to_path[member] = dest
        if member not in self.written:
            target = self.output_root / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.zf.read(member))
            self.written.add(member)
        occurrence["media_path"] = dest
        occurrence["rendered"] = True
        width, height = size
        dimensions = (f' width="{width}"' if width else "") + (
            f' height="{height}"' if height else ""
        )
        return (
            f'<img src="{html.escape(dest, quote=True)}" alt="{html.escape(Path(member).name, quote=True)}"{dimensions}/>',
            occurrence,
        )

    def _object(
        self, object_element: ET.Element, paragraph_index: int
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int, int]:
        ole_nodes = list(object_element.iter(f"{{{OFFICE}}}OLEObject"))
        shapes = list(object_element.iter(f"{{{V}}}shape"))
        image_refs: list[tuple[str, tuple[int | None, int | None], str | None]] = []
        seen_rel_ids: set[str] = set()
        for shape in shapes:
            shape_id = shape.get("id")
            size = _vml_size(shape)
            for image in shape.iter(f"{{{V}}}imagedata"):
                rel_id = image.get(R_ID)
                if rel_id and rel_id not in seen_rel_ids:
                    seen_rel_ids.add(rel_id)
                    image_refs.append((rel_id, size, shape_id))
        for image in object_element.iter(f"{{{V}}}imagedata"):
            rel_id = image.get(R_ID)
            if rel_id and rel_id not in seen_rel_ids:
                seen_rel_ids.add(rel_id)
                image_refs.append((rel_id, (None, None), None))

        image_markup: list[str] = []
        media_occurrences: list[dict[str, Any]] = []
        rendered_paths: dict[str, str] = {}
        for rel_id, size, shape_id in image_refs:
            associated_id = next(
                (ole.get("ShapeID") for ole in ole_nodes if ole.get("ShapeID") == shape_id), None
            )
            markup, occurrence = self._image(
                rel_id, paragraph_index, "ole_preview", associated_id or shape_id, size
            )
            image_markup.append(markup)
            if occurrence is not None:
                media_occurrences.append(occurrence)
                if occurrence["media_path"]:
                    rendered_paths[rel_id] = occurrence["media_path"]

        ole_occurrences: list[dict[str, Any]] = []
        for index, element in enumerate(ole_nodes):
            rel_id = element.get(R_ID)
            rel = self.rels.get(rel_id or "")
            source_target = rel.get("Target") if rel else None
            source_part = (
                None
                if not rel or rel.get("TargetMode") == "External"
                else _package_member(source_target or "")
            )
            shape_id = element.get("ShapeID") or rel_id or f"OLE-{paragraph_index}-{index}"
            shape = next(
                (node for node in shapes if node.get("id") == element.get("ShapeID")), None
            )
            preview_ids = []
            if shape is not None:
                preview_ids = list(
                    dict.fromkeys(
                        node.get(R_ID) for node in shape.iter(f"{{{V}}}imagedata") if node.get(R_ID)
                    )
                )
            if not preview_ids:
                preview_ids = [rid for rid, _, _ in image_refs]
            preview_paths = [rendered_paths[rid] for rid in preview_ids if rid in rendered_paths]
            ole = {
                "ole_id": shape_id,
                "ole_rel_id": rel_id,
                "source_part": source_part or source_target,
                "preview_rel_ids": preview_ids,
                "preview_media_paths": preview_paths,
                "prog_id": element.get("ProgID", ""),
            }
            if source_part and source_part in self.zf.namelist():
                if source_part not in self.native_equation_cache:
                    self.native_equation_cache[source_part] = extract_native_equation(
                        self.zf.read(source_part)
                    )
                ole["native_equation"] = dict(self.native_equation_cache[source_part])
            ole_occurrences.append(ole)
            if source_part is None or source_part not in self.zf.namelist():
                self.issues.append(
                    {
                        "code": "missing_ole_relationship",
                        "paragraph_index": paragraph_index,
                        "ole_id": shape_id,
                    }
                )
            if not preview_paths:
                self.issues.append(
                    {
                        "code": "ole_preview_missing",
                        "paragraph_index": paragraph_index,
                        "ole_id": shape_id,
                    }
                )
        if ole_nodes and not any(item["preview_media_paths"] for item in ole_occurrences):
            image_markup.append("<span>[嵌入公式预览缺失]</span>")
        source_count = len(image_refs)
        rendered_count = sum(bool(item.get("rendered")) for item in media_occurrences)
        return (
            "".join(image_markup),
            ole_occurrences,
            media_occurrences,
            source_count,
            rendered_count,
        )

    def paragraph(self, element: ET.Element, paragraph_index: int) -> dict[str, Any]:
        pieces: list[str] = []
        text_pieces: list[str] = []
        inline_runs: list[dict[str, Any]] = []
        media_paths: list[str] = []
        media_occurrences: list[dict[str, Any]] = []
        ole_occurrences: list[dict[str, Any]] = []
        omml_occurrences: list[dict[str, Any]] = []
        local_issues: list[dict[str, Any]] = []
        source_image_occurrences = 0
        rendered_image_occurrences = 0

        def walk(
            node: ET.Element,
            vml_size: tuple[int | None, int | None] = (None, None),
            drawing_size: tuple[int | None, int | None] = (None, None),
        ) -> None:
            nonlocal source_image_occurrences, rendered_image_occurrences
            if node.tag == W_T:
                value = node.text or ""
                escaped = html.escape(value, quote=False)
                pieces.append(escaped)
                inline_runs.append({"kind": "text", "text": value, "html": escaped})
                text_pieces.append(value)
            elif node.tag == W_TAB:
                pieces.append("&#160;&#160;&#160;&#160;")
                inline_runs.append(
                    {"kind": "text", "text": "\t", "html": "&#160;&#160;&#160;&#160;"}
                )
                text_pieces.append("\t")
            elif node.tag in {W_BR, W_CR}:
                pieces.append("<br/>")
                inline_runs.append({"kind": "text", "text": "\n", "html": "<br/>"})
                text_pieces.append("\n")
            elif node.tag == W_OBJECT:
                oles = list(node.iter(f"{{{OFFICE}}}OLEObject"))
                if oles:
                    markup, oles_found, images_found, source_count, rendered_count = self._object(
                        node, paragraph_index
                    )
                    pieces.append(markup)
                    inline_runs.append(
                        {
                            "kind": "html",
                            "text": "",
                            "html": markup,
                            "media_paths": [
                                item["media_path"] for item in images_found if item.get("rendered")
                            ],
                            "media_occurrences": images_found,
                            "ole_occurrences": oles_found,
                        }
                    )
                    ole_occurrences.extend(oles_found)
                    media_occurrences.extend(images_found)
                    source_image_occurrences += source_count
                    rendered_image_occurrences += rendered_count
                    media_paths.extend(
                        item["media_path"] for item in images_found if item["rendered"]
                    )
                    return
                for child in node:
                    walk(child, vml_size, drawing_size)
            elif node.tag in {
                "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline",
                "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor",
            }:
                child_size = _drawing_size(node)
                for child in node:
                    walk(child, vml_size, child_size)
            elif node.tag == f"{{{V}}}shape":
                child_size = _vml_size(node)
                for child in node:
                    walk(child, child_size, drawing_size)
            elif node.tag == f"{{{A}}}blip":
                rel_id = node.get(R_EMBED) or node.get(R_ID)
                source_image_occurrences += 1
                markup, occurrence = self._image(
                    rel_id, paragraph_index, "drawingml", size=drawing_size
                )
                pieces.append(markup)
                if occurrence is not None:
                    inline_runs.append(
                        {
                            "kind": "html",
                            "text": "",
                            "html": markup,
                            "media_paths": [occurrence["media_path"]]
                            if occurrence.get("rendered")
                            else [],
                            "media_occurrences": [occurrence],
                            "ole_occurrences": [],
                        }
                    )
                    media_occurrences.append(occurrence)
                    if occurrence["rendered"]:
                        rendered_image_occurrences += 1
                        media_paths.append(occurrence["media_path"])
            elif node.tag == f"{{{V}}}imagedata":
                rel_id = node.get(R_ID)
                source_image_occurrences += 1
                markup, occurrence = self._image(rel_id, paragraph_index, "vml", size=vml_size)
                pieces.append(markup)
                if occurrence is not None:
                    inline_runs.append(
                        {
                            "kind": "html",
                            "text": "",
                            "html": markup,
                            "media_paths": [occurrence["media_path"]]
                            if occurrence.get("rendered")
                            else [],
                            "media_occurrences": [occurrence],
                            "ole_occurrences": [],
                        }
                    )
                    media_occurrences.append(occurrence)
                    if occurrence["rendered"]:
                        rendered_image_occurrences += 1
                        media_paths.append(occurrence["media_path"])
            elif node.tag in {f"{{{M}}}oMath", f"{{{M}}}oMathPara"}:
                from .source_math import render_omml

                rendered = render_omml(node)
                self.omml_occurrence_index += 1
                supported = rendered.get("supported") is True
                occurrence = {
                    "occurrence": self.omml_occurrence_index,
                    "paragraph_index": paragraph_index,
                    "source_xml": rendered.get("source_xml", ET.tostring(node, encoding="unicode")),
                    "html": rendered.get("html", ""),
                    "text": rendered.get("text", ""),
                    "supported": supported,
                    "issues": rendered.get("issues", []),
                }
                omml_occurrences.append(occurrence)
                pieces.append(occurrence["html"])
                inline_runs.append(
                    {
                        "kind": "html",
                        "text": occurrence["text"],
                        "html": occurrence["html"],
                        "omml_occurrences": [occurrence],
                    }
                )
                text_pieces.append(occurrence["text"])
                if not supported:
                    local_issues.extend(
                        occurrence["issues"]
                        or [{"code": "omml_unsupported", "paragraph_index": paragraph_index}]
                    )
            else:
                for child in node:
                    walk(child, vml_size, drawing_size)

        before_issue_count = len(self.issues)
        walk(element)
        local_issues.extend(self.issues[before_issue_count:])
        text = "".join(text_pieces)
        return {
            "document_id": self.doc_id,
            "paragraph_index": paragraph_index,
            "source_paragraph_indices": [paragraph_index],
            "text": text,
            "html": "<p>" + "".join(pieces) + "</p>",
            "media_paths": media_paths,
            "media_occurrences": media_occurrences,
            "ole_ids": [item["ole_id"] for item in ole_occurrences],
            "ole_occurrences": ole_occurrences,
            "omml_occurrences": omml_occurrences,
            "source_omml_count": len(omml_occurrences),
            "rendered_omml_count": sum(item["supported"] for item in omml_occurrences),
            "inline_runs": inline_runs,
            "source_image_occurrences": source_image_occurrences,
            "rendered_image_occurrences": rendered_image_occurrences,
            "source_ole_count": len(ole_occurrences),
            "issues": local_issues,
        }


def _merge_blocks(
    blocks: list[dict[str, Any]], html_fragment: str | None = None, text_separator: str = "\n"
) -> dict[str, Any]:
    indices = sorted({i for block in blocks for i in block.get("source_paragraph_indices", [])})
    return {
        "document_id": blocks[0]["document_id"] if blocks else "",
        "paragraph_index": indices[0] if indices else 0,
        "source_paragraph_indices": indices,
        "text": text_separator.join(block["text"] for block in blocks),
        "html": html_fragment
        if html_fragment is not None
        else "".join(block["html"] for block in blocks),
        "media_paths": [path for block in blocks for path in block["media_paths"]],
        "media_occurrences": [item for block in blocks for item in block["media_occurrences"]],
        "ole_ids": [item for block in blocks for item in block["ole_ids"]],
        "ole_occurrences": [item for block in blocks for item in block["ole_occurrences"]],
        "omml_occurrences": [
            item for block in blocks for item in block.get("omml_occurrences", [])
        ],
        "source_image_occurrences": sum(block["source_image_occurrences"] for block in blocks),
        "rendered_image_occurrences": sum(block["rendered_image_occurrences"] for block in blocks),
        "source_ole_count": sum(block["source_ole_count"] for block in blocks),
        "source_omml_count": sum(block.get("source_omml_count", 0) for block in blocks),
        "rendered_omml_count": sum(block.get("rendered_omml_count", 0) for block in blocks),
        "inline_runs": [run for block in blocks for run in block.get("inline_runs", [])],
        "issues": [issue for block in blocks for issue in block["issues"]],
    }


def _plain_text(element: ET.Element) -> str:
    """Read paragraph text without a second pass over image/OLE relationships."""
    pieces: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == W_T:
            pieces.append(node.text or "")
        elif node.tag == W_TAB:
            pieces.append("\t")
        elif node.tag in {W_BR, W_CR}:
            pieces.append("\n")
        elif node.tag != W_OBJECT:
            for child in node:
                walk(child)

    walk(element)
    return "".join(pieces)


def _render_table(
    table: ET.Element, paragraph_indices: dict[int, int], renderer: _Renderer
) -> dict[str, Any]:
    row_html: list[str] = []
    row_texts: list[str] = []
    row_blocks: list[dict[str, Any]] = []
    for row in table.findall(f"./{W_TR}"):
        cells_html: list[str] = []
        cells_text: list[str] = []
        for cell in row.findall(f"./{W_TC}"):
            cell_html: list[str] = []
            cell_text: list[str] = []
            for child in cell:
                if child.tag == W_P:
                    block = renderer.paragraph(child, paragraph_indices[id(child)])
                    row_blocks.append(block)
                    cell_html.append(block["html"])
                    cell_text.append(block["text"])
                elif child.tag == W_TBL:
                    nested = _render_table(child, paragraph_indices, renderer)
                    row_blocks.append(nested)
                    cell_html.append(nested["html"])
                    cell_text.append(nested["text"])
            cells_html.append("<td>" + "".join(cell_html) + "</td>")
            cells_text.append("\n".join(cell_text))
        row_html.append("<tr>" + "".join(cells_html) + "</tr>")
        row_texts.append("\t".join(cells_text))
    all_indices = [i for block in row_blocks for i in block.get("source_paragraph_indices", [])]
    merged = _merge_blocks(
        row_blocks, "<table><tbody>" + "".join(row_html) + "</tbody></table>", "\n"
    )
    merged["text"] = "\n".join(row_texts)
    merged["paragraph_index"] = min(all_indices) if all_indices else 0
    merged["source_paragraph_indices"] = sorted(set(all_indices))
    return merged


def _read_relationships(zf: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    try:
        root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    return {
        item.get("Id", ""): dict(item.attrib)
        for item in root.findall(f"{{{PKG_REL}}}Relationship")
        if item.get("Id")
    }


def _section_type(text: str) -> str:
    if "多选" in text:
        return "multiple_choice"
    if "单选" in text or "选择" in text:
        return "single_choice"
    if "填空" in text:
        return "fill_blank"
    if "判断" in text:
        return "true_false"
    if "解答" in text:
        return "free_response"
    if "证明" in text:
        return "proof"
    return "unknown"


def _role_for_filename(path: Path) -> str:
    stem = unicodedata.normalize("NFKC", path.stem).strip()
    if stem.startswith("精品解析") or re.search(
        r"[（(]?(?:解析版|答案版|详解版)[）)]?$|(?:答案解析|试题解析)$", stem
    ):
        return "analysis"
    return "original"


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if not value or any(char not in _CHINESE_DIGITS and char not in {"十", "百"} for char in value):
        return None
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
        elif char == "十":
            section += (number or 1) * 10
            number = 0
        elif char == "百":
            section += (number or 1) * 100
            number = 0
    total += section + number
    return total or None


_DECLARED_COUNT_RE = re.compile(
    r"共\s*(\d+|[一二两三四五六七八九十百〇零]+)\s*(?:道)?\s*(?:小题|题)"
)


def _section_audits(document: _Document) -> list[dict[str, Any]]:
    starts = document.starts or {}
    heads = document.section_heads
    audits: list[dict[str, Any]] = []
    cumulative = 0
    for index, head in enumerate(heads):
        begin = head["paragraph_index"]
        end = (
            heads[index + 1]["paragraph_index"]
            if index + 1 < len(heads)
            else document.record["paragraph_count"]
        )
        declared = None
        for match in _DECLARED_COUNT_RE.finditer(head["text"]):
            declared = _chinese_number(match.group(1))
            if declared is not None:
                break
        numbers = sorted(
            number for number, paragraph_index in starts.items() if begin < paragraph_index < end
        )
        expected = (
            list(range(cumulative + 1, cumulative + declared + 1)) if declared is not None else None
        )
        if declared is not None:
            missing = sorted(set(expected) - set(numbers))
            cumulative += declared
            basis = "declared_section_count"
        else:
            missing = list(range(numbers[0], numbers[-1] + 1)) if numbers else []
            missing = sorted(set(missing) - set(numbers))
            basis = "observed_contiguous_range" if numbers else "unknown"
        audits.append(
            {
                "section": head["text"],
                "paragraph_index": begin,
                "declared_question_count": declared,
                "discovered_numbers": numbers,
                "missing_numbers": missing,
                "missing_number_basis": basis,
            }
        )
    return audits


def _make_document(
    source: Path, role: str, pair_id: str, title: str, doc_id: str, output_root: Path
) -> _Document:
    issues: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(source) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise DocxIngestError("source DOCX archive failed its CRC check")
            xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            rel_bytes = (
                zf.read("word/_rels/document.xml.rels")
                if "word/_rels/document.xml.rels" in zf.namelist()
                else b""
            )
            rels = _read_relationships(zf)
            body = root.find(f".//{{{W}}}body")
            if body is None:
                raise DocxIngestError("source DOCX has no document body")
            all_paragraphs = list(body.iter(W_P))
            paragraph_indices = {id(paragraph): i for i, paragraph in enumerate(all_paragraphs)}
            top_paragraph_ids = {id(child) for child in list(body) if child.tag == W_P}
            renderer = _Renderer(zf, rels, doc_id, output_root, issues)
            elements: list[_ElementBlock] = []
            paragraphs: list[dict[str, Any]] = []
            for child in list(body):
                if child.tag == W_P:
                    index = paragraph_indices[id(child)]
                    block = renderer.paragraph(child, index)
                    elements.append(_ElementBlock("paragraph", index, index, block))
                    paragraphs.append(
                        {
                            "paragraph_index": index,
                            "text": block["text"],
                            "top_level": id(child) in top_paragraph_ids,
                        }
                    )
                elif child.tag == W_TBL:
                    block = _render_table(child, paragraph_indices, renderer)
                    indices = block["source_paragraph_indices"]
                    start, end = (indices[0], indices[-1]) if indices else (0, 0)
                    elements.append(_ElementBlock("table", start, end, block))
                    for paragraph in child.iter(W_P):
                        block_index = paragraph_indices[id(paragraph)]
                        paragraphs.append(
                            {
                                "paragraph_index": block_index,
                                "text": _plain_text(paragraph),
                                "top_level": False,
                            }
                        )
            paragraph_count = len(all_paragraphs)
            section_heads = [
                paragraph
                for paragraph in paragraphs
                if paragraph["top_level"] and SECTION_START.match(paragraph["text"])
            ]
            media_members = [
                name
                for name in zf.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            ]
            ole_count = len(root.findall(f".//{{{OFFICE}}}OLEObject"))
            omml_count = len(root.findall(f".//{{{M}}}oMath"))
            digest = _sha256(source)
            xml_dir = output_root / "xml" / doc_id
            xml_dir.mkdir(parents=True, exist_ok=True)
            (xml_dir / "document.xml").write_bytes(xml_bytes)
            if rel_bytes:
                (xml_dir / "document.xml.rels").write_bytes(rel_bytes)
    except DocxIngestError:
        raise
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError):
        raise DocxIngestError(
            f"could not import DOCX {source.name!r}: invalid or unreadable package"
        ) from None

    record = {
        "id": doc_id,
        "filename": source.name,
        "sha256": digest,
        "role": role,
        "pair_id": pair_id,
        "status": "imported",
        "paragraph_count": paragraph_count,
        "media_count": len(media_members),
        "ole_count": ole_count,
        "source_omml_count": omml_count,
        "rendered_omml_count": sum(
            block.get("rendered_omml_count", 0) for el in elements for block in [el.block]
        ),
        "source_image_occurrences": sum(
            block["source_image_occurrences"] for el in elements for block in [el.block]
        ),
        "rendered_image_occurrences": sum(
            block["rendered_image_occurrences"] for el in elements for block in [el.block]
        ),
        "imported_ole_count": sum(
            block["source_ole_count"] for el in elements for block in [el.block]
        ),
        "excluded_leading_paragraph_count": 0,
        "excluded_leading_source_range": None,
    }
    return _Document(
        record=record,
        paragraphs=sorted(paragraphs, key=lambda item: item["paragraph_index"]),
        elements=elements,
        issues=issues,
        section_heads=section_heads,
        starts={},
        duplicates={},
        declared_question_counts=[],
    )


def _find_starts(document: _Document, role: str, expected_numbers: list[int] | None = None) -> None:
    if not document.section_heads:
        document.issues.append(
            {"code": "question_section_not_found", "document_id": document.record["id"]}
        )
        document.record["excluded_leading_paragraph_count"] = document.record["paragraph_count"]
        document.record["status"] = "processing_failed"
        return
    first_section_index = document.section_heads[0]["paragraph_index"]
    document.first_question_index = first_section_index
    candidates: list[tuple[int, int]] = []
    for paragraph in document.paragraphs:
        if not paragraph["top_level"] or paragraph["paragraph_index"] <= first_section_index:
            continue
        match = QUESTION_START.match(paragraph["text"])
        if match:
            candidates.append((paragraph["paragraph_index"], int(match.group(1))))
    if role == "analysis":
        answer_positions = [
            item["paragraph_index"]
            for item in document.paragraphs
            if item["top_level"]
            and item["paragraph_index"] > first_section_index
            and "【答案】" in item["text"]
        ]
        selected: dict[int, int] = {}
        duplicates: dict[int, list[int]] = {}
        previous_start = first_section_index
        for answer_pos in answer_positions:
            interval = [(idx, num) for idx, num in candidates if previous_start < idx <= answer_pos]
            if interval:
                start_idx, number = interval[-1]
                if number in selected:
                    duplicates.setdefault(number, [selected[number]]).append(start_idx)
                elif number > (max(selected, default=0)):
                    selected[number] = start_idx
                previous_start = start_idx
        # Preserve intermediate numbered stems that have no answer marker.
        # These must bound their own failed record instead of being swallowed
        # into the preceding answered question's explanation.
        anchored = sorted(selected.items(), key=lambda item: item[1])
        if anchored:
            first_number, first_pos = anchored[0]
            for idx, number in candidates:
                if idx < first_pos and 0 < number < first_number:
                    selected.setdefault(number, idx)
        for (previous_number, previous_pos), (next_number, next_pos) in zip(anchored, anchored[1:]):
            missing_candidates = [
                (idx, number)
                for idx, number in candidates
                if previous_pos < idx < next_pos and previous_number < number < next_number
            ]
            for idx, number in missing_candidates:
                selected.setdefault(number, idx)
        if anchored:
            last_number, last_pos = anchored[-1]
            trailing = [
                (idx, number)
                for idx, number in candidates
                if idx > last_pos and number == last_number + 1
            ]
            if trailing:
                selected.setdefault(last_number + 1, trailing[0][0])
        document.starts = selected
        document.duplicates = duplicates
        first_start = min(selected.values()) if selected else None
        if first_start is not None:
            document.record["excluded_leading_paragraph_count"] = first_start
            if first_start:
                document.record["excluded_leading_source_range"] = [0, first_start - 1]
        if not selected:
            document.record["status"] = "processing_failed"
            document.issues.append(
                {"code": "no_question_numbers_found", "document_id": document.record["id"]}
            )
        document.declared_question_counts = _section_audits(document)
        document.record["section_audits"] = document.declared_question_counts
        declared_values = [
            item["declared_question_count"]
            for item in document.declared_question_counts
            if item["declared_question_count"] is not None
        ]
        document.record["declared_question_count"] = (
            sum(declared_values)
            if declared_values and len(declared_values) == len(document.declared_question_counts)
            else None
        )
        return
    selected: dict[int, int] = {}
    duplicates: dict[int, list[int]] = {}
    last_number = 0
    for index, number in candidates:
        if number in selected:
            duplicates.setdefault(number, [selected[number]]).append(index)
            continue
        if number <= last_number:
            # Repeated/backward numeric prose inside a solution is not a new
            # top-level question. Crucially, original numbers never filter this scan.
            continue
        selected[number] = index
        last_number = number
    document.starts = selected
    document.duplicates = duplicates
    first_start = min(document.starts.values()) if document.starts else None
    if first_start is not None:
        document.record["excluded_leading_paragraph_count"] = first_start
        if first_start:
            document.record["excluded_leading_source_range"] = [0, first_start - 1]
    if not selected:
        document.record["status"] = "processing_failed"
        document.issues.append(
            {"code": "no_question_numbers_found", "document_id": document.record["id"]}
        )
    document.declared_question_counts = _section_audits(document)
    document.record["section_audits"] = document.declared_question_counts
    declared_values = [
        item["declared_question_count"]
        for item in document.declared_question_counts
        if item["declared_question_count"] is not None
    ]
    document.record["declared_question_count"] = (
        sum(declared_values)
        if declared_values and len(declared_values) == len(document.declared_question_counts)
        else None
    )


def _segment_range(document: _Document, number: int) -> tuple[int, int] | None:
    starts = document.starts or {}
    if number not in starts:
        return None
    start = starts[number]
    subsequent = [index for index in starts.values() if index > start]
    end_exclusive = min(subsequent) if subsequent else document.record["paragraph_count"]
    section_after = [
        item["paragraph_index"]
        for item in document.section_heads
        if start < item["paragraph_index"] < end_exclusive
    ]
    if section_after:
        end_exclusive = min(section_after)
    return (start, max(start, end_exclusive - 1))


def _blocks_in_range(document: _Document, span: tuple[int, int] | None) -> list[dict[str, Any]]:
    if span is None:
        return []
    start, end = span
    result: list[dict[str, Any]] = []
    for element in document.elements:
        if element.end_paragraph < start or element.start_paragraph > end:
            continue
        block = element.block
        if element.kind == "table":
            indices = block.get("source_paragraph_indices", [])
            if indices and (min(indices) < start or max(indices) > end):
                block = dict(block)
                block["issues"] = [
                    *block["issues"],
                    {
                        "code": "table_crosses_question_boundary",
                        "paragraph_index": block["paragraph_index"],
                    },
                ]
        if block["text"].strip() or block["media_paths"] or block["ole_ids"]:
            result.append(block)
    return result


def _compare_association(
    source_blocks: list[dict[str, Any]], analysis_blocks: list[dict[str, Any]], root: Path
) -> tuple[str, dict[str, Any]]:
    return compare_association(source_blocks, analysis_blocks, root)


def _split_analysis(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    stems: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []
    has_answer_label = False
    segment_index = 0
    current_state = "stem"
    for block in blocks:
        if block.get("html", "").startswith("<table"):
            markers = list(LABEL_RE.finditer(block["text"]))
            routed = dict(block)
            routed["segment_index"] = segment_index
            routed["source_text_range"] = [0, len(block["text"])]
            if markers:
                routed.setdefault("issues", []).append(
                    {
                        "code": "marker_in_table_block",
                        "paragraph_index": block.get("paragraph_index"),
                    }
                )
                for marker in markers:
                    segment_index += 1
                    current_state = ANSWER_LABELS[marker.group(0)]
                routed["marker_occurrences"] = [
                    {"marker": m.group(0), "start": m.start(), "end": m.end()} for m in markers
                ]
                routed["segment_index"] = segment_index
            if current_state == "stem":
                stems.append(routed)
            elif current_state == "answer":
                answers.append(routed)
                has_answer_label |= bool(markers)
            else:
                analyses.append(routed)
            continue
        source_runs = block.get("inline_runs") or []
        if not source_runs:
            if LABEL_RE.search(block["text"]):
                block = dict(block)
                block.setdefault("issues", []).append(
                    {
                        "code": "marker_in_nonparagraph_block",
                        "paragraph_index": block.get("paragraph_index"),
                    }
                )
                marker = list(LABEL_RE.finditer(block["text"]))[-1]
                state = ANSWER_LABELS[marker.group(0)]
                current_state = state
                segment_index += len(list(LABEL_RE.finditer(block["text"])))
            else:
                state = current_state
            routed = dict(block)
            routed["segment_index"] = segment_index
            routed["source_text_range"] = [0, len(block["text"])]
            if state == "stem":
                stems.append(routed)
            elif state == "answer":
                answers.append(routed)
            else:
                analyses.append(routed)
            has_answer_label |= any(
                ANSWER_LABELS[m.group(0)] == "answer" for m in LABEL_RE.finditer(block["text"])
            )
            continue

        events: list[dict[str, Any]] = []
        raw_parts: list[str] = []
        char_to_event: dict[int, int] = {}
        char_position = 0
        for run in source_runs:
            value = run.get("text", "")
            markup = run.get("html", "")
            if run.get("kind") == "text":
                for offset, char in enumerate(value):
                    char_markup = (
                        "&#160;&#160;&#160;&#160;"
                        if char == "\t"
                        else "<br/>"
                        if char == "\n"
                        else html.escape(char, quote=False)
                    )
                    event_index = len(events)
                    char_to_event[char_position] = event_index
                    events.append({"text": char, "html": char_markup, "metadata": {}})
                    raw_parts.append(char)
                    char_position += 1
            else:
                event_index = len(events)
                char_to_event[char_position] = event_index
                events.append(
                    {
                        "text": value,
                        "html": markup,
                        "metadata": {
                            key: run.get(key, [])
                            for key in (
                                "media_paths",
                                "media_occurrences",
                                "ole_occurrences",
                                "omml_occurrences",
                            )
                        },
                    }
                )
                raw_parts.append(value)
                char_position += len(value)
        raw_text = "".join(raw_parts)
        matches = list(LABEL_RE.finditer(raw_text))
        match_events = {char_to_event.get(match.start(), len(events)): match for match in matches}
        segment_records: list[dict[str, Any]] = []
        state = current_state
        current = {
            "state": state,
            "events": [],
            "markers": [],
            "start": 0,
            "segment_index": segment_index,
        }
        segment_records.append(current)
        char_position = 0
        for event_index, event in enumerate(events):
            match = match_events.get(event_index)
            if match:
                segment_index += 1
                if current["events"] or current["markers"]:
                    current["end"] = char_position
                    state = ANSWER_LABELS[match.group(0)]
                    current = {
                        "state": state,
                        "events": [],
                        "markers": [],
                        "start": match.start(),
                        "segment_index": segment_index,
                    }
                    segment_records.append(current)
                else:
                    state = ANSWER_LABELS[match.group(0)]
                    current["state"] = state
                    current["start"] = match.start()
                    current["segment_index"] = segment_index
                current["markers"].append(
                    {"marker": match.group(0), "start": match.start(), "end": match.end()}
                )
                has_answer_label |= state == "answer"
            current["events"].append(event)
            char_position += len(event["text"])
        current["end"] = char_position
        current_state = state
        for record in segment_records:
            if not record["events"] and not record["markers"]:
                continue
            segment_text = "".join(event["text"] for event in record["events"])
            segment_html = "".join(event["html"] for event in record["events"])
            routed = dict(block)
            routed.update(
                {
                    "text": segment_text,
                    "html": "<p>" + segment_html + "</p>",
                    "source_text_range": [record["start"], record.get("end", char_position)],
                    "segment_index": record["segment_index"],
                    "marker_occurrences": record["markers"],
                    "inline_runs": [
                        {"kind": "text", "text": event["text"], "html": event["html"]}
                        for event in record["events"]
                    ],
                    "media_paths": [
                        p
                        for event in record["events"]
                        for p in event["metadata"].get("media_paths", [])
                    ],
                    "media_occurrences": [
                        m
                        for event in record["events"]
                        for m in event["metadata"].get("media_occurrences", [])
                    ],
                    "ole_occurrences": [
                        o
                        for event in record["events"]
                        for o in event["metadata"].get("ole_occurrences", [])
                    ],
                    "omml_occurrences": [
                        o
                        for event in record["events"]
                        for o in event["metadata"].get("omml_occurrences", [])
                    ],
                }
            )
            routed["ole_ids"] = [item["ole_id"] for item in routed["ole_occurrences"]]
            routed["source_image_occurrences"] = len(routed["media_occurrences"])
            routed["rendered_image_occurrences"] = sum(
                bool(item.get("rendered")) for item in routed["media_occurrences"]
            )
            routed["source_ole_count"] = len(routed["ole_occurrences"])
            routed["source_omml_count"] = len(routed["omml_occurrences"])
            routed["rendered_omml_count"] = sum(
                item.get("supported") is True for item in routed["omml_occurrences"]
            )
            if record["state"] == "stem":
                stems.append(routed)
            elif record["state"] == "answer":
                answers.append(routed)
            else:
                analyses.append(routed)
    return stems, answers, analyses, has_answer_label


def _pair_title(path: Path) -> tuple[str, str]:
    key, title = _normalize_pair_name(path.name)
    pair_id = "pair-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return pair_id, title


def build_source_bank(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Import every top-level DOCX in ``source_dir`` and atomically create a new bank dir.

    Existing output paths are rejected. The source tree cannot contain the output,
    and source files are copied byte-for-byte under output/sources/.
    """
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if not source_dir.is_dir():
        raise DocxIngestError("source directory does not exist")
    if (
        source_dir == output_dir
        or source_dir in output_dir.parents
        or output_dir in source_dir.parents
    ):
        raise DocxIngestError("source and output directories must not overlap")
    if os.path.lexists(output_dir):
        raise DocxIngestError("output directory already exists; refusing to overwrite")
    sources = sorted(
        (path for path in source_dir.iterdir() if path.suffix.lower() == ".docx"),
        key=lambda path: path.name,
    )
    if not sources:
        raise DocxIngestError("source directory contains no DOCX files")
    if any(path.is_symlink() for path in sources):
        raise DocxIngestError("symbolic-link DOCX sources are not supported")
    output_parent = output_dir.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_parent / f".{output_dir.name}.import.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise DocxIngestError("another import is already publishing this output") from None
    os.close(lock_fd)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.stage-", dir=output_parent))
    try:
        (stage / "sources").mkdir()
        (stage / "xml").mkdir()
        doc_entries: dict[tuple[str, str, str], dict[str, Any]] = {}
        failed_documents: list[dict[str, Any]] = []
        duplicate_files = 0
        for source in sources:
            role = _role_for_filename(source)
            pair_id, title = _pair_title(source)
            try:
                source_digest = _sha256(source)
            except OSError:
                source_digest = None
            if source_digest is None:
                failed_documents.append(
                    {
                        "filename": source.name,
                        "sha256": None,
                        "role": role,
                        "pair_id": pair_id,
                        "code": "source_unreadable",
                    }
                )
                continue
            signature = (pair_id, role, source_digest)
            if signature in doc_entries:
                entry = doc_entries[signature]
                entry["aliases"].append(source.name)
                entry["sources"].append(source)
                duplicate_files += 1
            else:
                identity = hashlib.sha256("\0".join(signature).encode("utf-8")).hexdigest()[:16]
                doc_entries[signature] = {
                    "id": f"doc-{identity}",
                    "role": role,
                    "pair_id": pair_id,
                    "title": title,
                    "source": source,
                    "sources": [source],
                    "aliases": [],
                    "sha256": source_digest,
                }
        source_copy_records: dict[Path, str] = {}
        for entry in doc_entries.values():
            for source in entry["sources"]:
                rel_path = f"sources/{source.name}"
                shutil.copyfile(source, stage / rel_path)
                source_copy_records[source] = rel_path
        for item in failed_documents:
            source = next((p for p in sources if p.name == item["filename"]), None)
            if source is not None:
                rel_path = f"sources/{source.name}"
                if not (stage / rel_path).exists():
                    try:
                        shutil.copyfile(source, stage / rel_path)
                        item["source_copy_path"] = rel_path
                    except OSError:
                        pass
        documents: list[_Document] = []
        for entry in doc_entries.values():
            source = entry["source"]
            try:
                doc = _make_document(
                    source, entry["role"], entry["pair_id"], entry["title"], entry["id"], stage
                )
            except (DocxIngestError, OSError):
                failed_documents.append(
                    {
                        "filename": source.name,
                        "sha256": entry["sha256"],
                        "role": entry["role"],
                        "pair_id": entry["pair_id"],
                        "code": "invalid_or_unreadable_docx",
                        "source_copy_path": source_copy_records[source],
                        "aliases": list(entry["aliases"]),
                        "source_copies": [source_copy_records[p] for p in entry["sources"]],
                    }
                )
                continue
            doc.record["aliases"] = list(entry["aliases"])
            doc.record["source_copies"] = [
                {"filename": p.name, "path": source_copy_records[p]} for p in entry["sources"]
            ]
            doc.record["sha256"] = entry["sha256"]
            documents.append(doc)

        grouped: dict[str, dict[str, Any]] = {}
        for doc in documents:
            group = grouped.setdefault(
                doc.record["pair_id"], {"title": _pair_title(Path(doc.record["filename"]))[1]}
            )
            key = doc.record["role"]
            if key in group:
                group.setdefault("duplicate_documents", []).append(doc)
            else:
                group[key] = doc
        global_issues: list[dict[str, Any]] = []
        pairs: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        for pair_id, group in sorted(grouped.items()):
            original = group.get("original")
            analysis = group.get("analysis")
            if original:
                _find_starts(original, "original")
            if analysis:
                _find_starts(analysis, "analysis")
            for duplicate_doc in group.get("duplicate_documents", []):
                global_issues.append(
                    {
                        "code": "duplicate_pair_role",
                        "pair_id": pair_id,
                        "document_id": duplicate_doc.record["id"],
                        "role": duplicate_doc.record["role"],
                    }
                )
            if not original or not analysis:
                global_issues.append(
                    {
                        "code": "missing_pair_document",
                        "pair_id": pair_id,
                        "missing_role": "original" if not original else "analysis",
                    }
                )
            original_numbers = set(original.starts or {}) if original else set()
            analysis_numbers = set(analysis.starts or {}) if analysis else set()
            all_numbers = sorted(original_numbers | analysis_numbers)
            pair_questions: list[dict[str, Any]] = []
            missing_original = sorted(analysis_numbers - original_numbers)
            missing_analysis = sorted(original_numbers - analysis_numbers)
            duplicates_original = sorted((original.duplicates or {}).keys()) if original else []
            duplicates_analysis = sorted((analysis.duplicates or {}).keys()) if analysis else []
            for number in all_numbers:
                source_span = _segment_range(original, number) if original else None
                analysis_span = _segment_range(analysis, number) if analysis else None
                stem_blocks = _blocks_in_range(original, source_span) if original else []
                analysis_source_blocks = (
                    _blocks_in_range(analysis, analysis_span) if analysis else []
                )
                analysis_stems, answer_blocks, analysis_blocks, has_answer_label = _split_analysis(
                    analysis_source_blocks
                )
                q_issues: list[dict[str, Any]] = []
                if source_span is None:
                    q_issues.append({"code": "missing_original_question", "number": number})
                if analysis_span is None:
                    q_issues.append({"code": "missing_analysis_question", "number": number})
                if number in duplicates_original:
                    q_issues.append(
                        {
                            "code": "duplicate_original_question_number",
                            "number": number,
                            "paragraph_indices": original.duplicates[number],
                        }
                    )
                if number in duplicates_analysis:
                    q_issues.append(
                        {
                            "code": "duplicate_analysis_question_number",
                            "number": number,
                            "paragraph_indices": analysis.duplicates[number],
                        }
                    )
                answer_available = False
                for block in answer_blocks:
                    plain = LABEL_RE.sub("", block["text"]).strip()
                    formula_text = "".join(
                        item.get("text", "") for item in block.get("omml_occurrences", [])
                    )
                    plain_without_formula = (
                        plain.replace(formula_text, "").strip() if formula_text else plain
                    )
                    valid_math = any(
                        item.get("supported") is True for item in block.get("omml_occurrences", [])
                    )
                    answer_available |= bool(
                        plain_without_formula or block.get("media_paths") or valid_math
                    )
                if not has_answer_label or not answer_available:
                    q_issues.append({"code": "missing_answer", "number": number})
                association_status = "unverified"
                association_evidence = {
                    "text_match": False,
                    "source_image_count": 0,
                    "analysis_image_count": 0,
                    "image_pixels_match": None,
                    "omml_structure_match": None,
                }
                if source_span is not None and analysis_span is not None:
                    association_status, association_evidence = _compare_association(
                        stem_blocks, analysis_stems, stage
                    )
                    if association_status == "unverified":
                        q_issues.append({"code": "association_unverified", "number": number})
                all_blocks = [*stem_blocks, *analysis_stems, *answer_blocks, *analysis_blocks]
                block_issues = [issue for block in all_blocks for issue in block.get("issues", [])]
                q_issues.extend(block_issues)
                if any(
                    issue["code"] == "table_crosses_question_boundary" for issue in block_issues
                ):
                    q_issues.append({"code": "incomplete_table_segmentation", "number": number})
                section_record = None
                section_document = original if original and source_span else analysis
                section_span = source_span if original and source_span else analysis_span
                if section_document and section_span:
                    eligible_heads = [
                        head
                        for head in section_document.section_heads
                        if head["paragraph_index"] < section_span[0]
                    ]
                    if eligible_heads:
                        section_record = eligible_heads[-1]
                section_text = section_record["text"] if section_record else ""
                structural_issues = [
                    issue for issue in q_issues if issue["code"] != "association_unverified"
                ]
                imported = not structural_issues
                question = {
                    "id": f"{pair_id}-q{number}",
                    "pair_id": pair_id,
                    "number": number,
                    "question_origin": "paired" if source_span is not None else "analysis_only",
                    "question_type": _section_type(section_text),
                    "section": section_text,
                    "stem_blocks": stem_blocks,
                    "analysis_stem_blocks": analysis_stems,
                    "answer_blocks": answer_blocks,
                    "analysis_blocks": analysis_blocks,
                    "structure_status": "imported" if imported else "processing_failed",
                    "association_status": association_status,
                    "association_evidence": association_evidence,
                    "association_comparison_method": "source_association_v3",
                    "answer_available": bool(has_answer_label and answer_available),
                    "issues": q_issues,
                    "practice_ready": False,
                    "difficulty_score": None,
                    "source_question_range": list(source_span) if source_span else None,
                    "analysis_question_range": list(analysis_span) if analysis_span else None,
                    "source_image_occurrences": sum(
                        b["source_image_occurrences"] for b in stem_blocks
                    ),
                    "rendered_image_occurrences": sum(
                        b["rendered_image_occurrences"] for b in stem_blocks
                    ),
                    "source_ole_count": sum(b["source_ole_count"] for b in stem_blocks),
                    "analysis_source_image_occurrences": sum(
                        b["source_image_occurrences"] for b in analysis_source_blocks
                    ),
                    "analysis_rendered_image_occurrences": sum(
                        b["rendered_image_occurrences"] for b in analysis_source_blocks
                    ),
                    "analysis_source_ole_count": sum(
                        b["source_ole_count"] for b in analysis_source_blocks
                    ),
                }
                pair_questions.append(question)
                questions.append(question)
            pair_issue_list: list[dict[str, Any]] = []
            if missing_original:
                pair_issue_list.append(
                    {"code": "analysis_questions_without_original", "numbers": missing_original}
                )
            if missing_analysis:
                pair_issue_list.append(
                    {"code": "original_questions_without_analysis", "numbers": missing_analysis}
                )
            if duplicates_original:
                pair_issue_list.append(
                    {"code": "duplicate_original_question_numbers", "numbers": duplicates_original}
                )
            if duplicates_analysis:
                pair_issue_list.append(
                    {"code": "duplicate_analysis_question_numbers", "numbers": duplicates_analysis}
                )
            original_audits = original.record.get("section_audits", []) if original else []
            analysis_audits = analysis.record.get("section_audits", []) if analysis else []
            declared_original = original.record.get("declared_question_count") if original else None
            declared_analysis = analysis.record.get("declared_question_count") if analysis else None
            original_section_counts = [
                item.get("declared_question_count") for item in original_audits
            ]
            analysis_section_counts = [
                item.get("declared_question_count") for item in analysis_audits
            ]
            declarations_agree = bool(
                original_section_counts
                and len(original_section_counts) == len(analysis_section_counts)
                and all(value is not None for value in original_section_counts)
                and original_section_counts == analysis_section_counts
            )
            if (
                original_section_counts
                and analysis_section_counts
                and all(value is not None for value in original_section_counts)
                and all(value is not None for value in analysis_section_counts)
                and not declarations_agree
            ):
                pair_issue_list.append(
                    {
                        "code": "declared_section_counts_mismatch",
                        "original": original_section_counts,
                        "analysis": analysis_section_counts,
                    }
                )
            if (
                declared_original is not None
                and declared_analysis is not None
                and declared_original != declared_analysis
            ):
                pair_issue_list.append(
                    {
                        "code": "declared_question_count_mismatch",
                        "original": declared_original,
                        "analysis": declared_analysis,
                    }
                )
            if declarations_agree:
                missing_basis = "agreed_section_declarations"
                expected_count = sum(original_section_counts)
                declared_question_count = expected_count
            else:
                missing_basis = "observed_1_to_max_inference" if all_numbers else "unknown"
                expected_count = max(all_numbers) if all_numbers else None
                declared_question_count = None
            common_missing = (
                sorted(set(range(1, expected_count + 1)) - set(all_numbers))
                if expected_count is not None
                else []
            )
            if common_missing:
                pair_issue_list.append(
                    {
                        "code": "common_missing_question_numbers",
                        "numbers": common_missing,
                        "basis": missing_basis,
                    }
                )
            if original:
                original.record["discovered_question_numbers"] = sorted(original_numbers)
            if analysis:
                analysis.record["discovered_question_numbers"] = sorted(analysis_numbers)
            pairs.append(
                {
                    "id": pair_id,
                    "title": group["title"],
                    "original_document_id": original.record["id"] if original else None,
                    "analysis_document_id": analysis.record["id"] if analysis else None,
                    "question_count": len(pair_questions),
                    "original_question_count": len(original_numbers),
                    "analysis_question_count": len(analysis_numbers),
                    "original_discovered_numbers": sorted(original_numbers),
                    "analysis_discovered_numbers": sorted(analysis_numbers),
                    "missing_original_numbers": missing_original,
                    "missing_analysis_numbers": missing_analysis,
                    "original_without_analysis": missing_analysis,
                    "analysis_without_original": missing_original,
                    "declared_question_count": declared_question_count,
                    "inferred_number_ceiling": expected_count
                    if declared_question_count is None
                    else None,
                    "common_missing_numbers": common_missing,
                    "common_missing_number_basis": missing_basis,
                    "original_section_audits": original_audits,
                    "analysis_section_audits": analysis_audits,
                    "duplicate_original_numbers": duplicates_original,
                    "duplicate_analysis_numbers": duplicates_analysis,
                    "issues": pair_issue_list,
                }
            )
            global_issues.extend(pair_issue_list)
            if original:
                global_issues.extend(original.issues)
            if analysis:
                global_issues.extend(analysis.issues)
        for doc in documents:
            global_issues.extend(doc.issues)
        for failed in failed_documents:
            global_issues.append(
                {
                    "code": "failed_document",
                    "filename": failed["filename"],
                    "reason_code": failed["code"],
                }
            )
        processing_failed_questions = sum(
            q["structure_status"] == "processing_failed" for q in questions
        )
        unverified_questions = sum(q.get("association_status") == "unverified" for q in questions)
        if not questions:
            batch_status = "failed"
        elif (
            failed_documents or processing_failed_questions or unverified_questions or global_issues
        ):
            batch_status = "partial"
        else:
            batch_status = "succeeded"
        bank = {
            "schema_version": "source-bank-v3",
            "association_version": "source_association_v3",
            "batch_status": batch_status,
            "documents": [doc.record for doc in documents],
            "failed_documents": failed_documents,
            "pairs": pairs,
            "questions": questions,
            "issues": global_issues,
            "counts": {
                "input_files": len(sources),
                "duplicate_files": duplicate_files,
                "failed_files": len(failed_documents),
                "documents": len(documents),
                "pairs": len(pairs),
                "questions": len(questions),
                "imported_questions": sum(q["structure_status"] == "imported" for q in questions),
                "processing_failed_questions": processing_failed_questions,
                "association_unverified_questions": unverified_questions,
                "answer_available_questions": sum(q["answer_available"] for q in questions),
                "source_image_occurrences": sum(
                    doc.record["source_image_occurrences"] for doc in documents
                ),
                "rendered_image_occurrences": sum(
                    doc.record["rendered_image_occurrences"] for doc in documents
                ),
                "ole_count": sum(doc.record["ole_count"] for doc in documents),
                "imported_ole_count": sum(doc.record["imported_ole_count"] for doc in documents),
                "source_omml_count": sum(doc.record["source_omml_count"] for doc in documents),
                "rendered_omml_count": sum(doc.record["rendered_omml_count"] for doc in documents),
            },
        }
        (stage / "bank.json").write_text(
            json.dumps(bank, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if os.path.lexists(output_dir):
            raise DocxIngestError("output directory appeared during import; refusing to overwrite")
        os.rename(stage, output_dir)
        return bank
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
