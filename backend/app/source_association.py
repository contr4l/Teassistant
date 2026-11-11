"""Fail-closed comparison of source and analysis question content.

The comparison preserves the order of text, tables, images, and OMML. Native
MathType payload identity may stand in for its bound preview image; ordinary
images still require exact RGBA equality.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict, deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from PIL import Image

_NATIVE_METHOD = "native_mtef_exact_v1"
_HEX_256 = re.compile(r"^[0-9a-fA-F]{64}$")
_QUESTION_PREFIX = re.compile(r"^\s*(?:第\d+题|\d+[.、)]\s+)")
_TABLE_TAGS = {"table", "tbody", "tr", "td", "th"}
_MEDIA_KINDS = {"ole_preview", "drawingml", "vml"}


class _BlockEventsParser(HTMLParser):
    """Translate importer-generated safe HTML into ordered comparison events."""

    def __init__(
        self, block: dict[str, Any], image_cache: dict[Path, tuple[tuple[int, int], bytes]]
    ):
        super().__init__(convert_charrefs=True)
        self.block = block
        self.image_cache = image_cache
        self.events: list[dict[str, Any]] = []
        self.issues: list[dict[str, str]] = []
        self.media = list(block.get("media_occurrences", []))
        self.oles = list(block.get("ole_occurrences", []))
        self.omml = list(block.get("omml_occurrences", []))
        self.used_media: set[int] = set()
        self.used_oles: set[int] = set()
        self.media_by_path: dict[str, deque[int]] = defaultdict(deque)
        for index, occurrence in enumerate(self.media):
            path = occurrence.get("media_path")
            if isinstance(path, str) and path:
                self.media_by_path[path].append(index)
        self.oles_by_id: dict[str, list[int]] = defaultdict(list)
        for index, occurrence in enumerate(self.oles):
            ole_id = occurrence.get("ole_id")
            if isinstance(ole_id, str) and ole_id:
                self.oles_by_id[ole_id].append(index)
        self.omml_queue = deque(range(len(self.omml)))
        self.math_capture: dict[str, Any] | None = None
        self.math_depth = 0
        self.unsupported_span_capture: dict[str, Any] | None = None
        self.span_text: list[str] | None = None

    def _provenance(self, occurrence: dict[str, Any] | None) -> dict[str, Any]:
        if occurrence is None:
            return {
                "document_id": self.block.get("document_id"),
                "paragraph_index": self.block.get("paragraph_index"),
            }
        return {
            "document_id": self.block.get("document_id"),
            "paragraph_index": occurrence.get("paragraph_index", self.block.get("paragraph_index")),
            "occurrence": occurrence.get("occurrence"),
            "source_rel_id": occurrence.get("source_rel_id"),
            "source_part": occurrence.get("source_part"),
            "media_path": occurrence.get("media_path"),
            "ole_id": occurrence.get("ole_id"),
            "kind": occurrence.get("kind"),
        }

    def _native(self, ole: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None, str | None]:
        if ole is None:
            return "unavailable", None, "ole_binding_missing"
        native = ole.get("native_equation")
        if not isinstance(native, dict):
            return "unavailable", None, "native_metadata_missing"
        if native.get("status") != "valid":
            reason = native.get("reason_code")
            return "unavailable", native, str(reason or native.get("status") or "native_invalid")
        # Once an entry claims to be valid, malformed metadata is a hard failure;
        # it must not be reinterpreted as an unavailable native stream.
        valid = (
            native.get("method") == _NATIVE_METHOD
            and isinstance(native.get("payload_sha256"), str)
            and _HEX_256.fullmatch(native["payload_sha256"]) is not None
            and type(native.get("payload_size")) is int
            and native["payload_size"] > 0
            and type(native.get("mtef_version")) is int
            and native["mtef_version"] == 5
            and isinstance(native.get("ole_sha256"), str)
            and _HEX_256.fullmatch(native["ole_sha256"]) is not None
            and native.get("reason_code") is None
        )
        if not valid:
            return "declared_invalid", native, "native_metadata_malformed"
        return "valid", native, None

    def _image_signature(self, occurrence: dict[str, Any] | None) -> tuple[Any, str | None]:
        if occurrence is None:
            return None, "media_occurrence_missing"
        if occurrence.get("rendered") is not True:
            return None, "media_not_rendered"
        relative = occurrence.get("media_path")
        if not isinstance(relative, str) or not relative:
            return None, "media_path_missing"
        if "://" in relative or relative.startswith(("/", "\\")):
            return None, "media_path_not_relative"
        try:
            root = Path(self.block.get("_asset_root", "."))
            resolved_root = root.resolve(strict=True)
            resolved = (resolved_root / relative).resolve(strict=True)
            resolved.relative_to(resolved_root)
            if not resolved.is_file():
                return None, "media_file_missing"
            if resolved not in self.image_cache:
                with Image.open(resolved) as image:
                    rgba = image.convert("RGBA")
                    self.image_cache[resolved] = (rgba.size, rgba.tobytes())
            return self.image_cache[resolved], None
        except (OSError, ValueError, RuntimeError):
            return None, "media_file_invalid_or_escaped"

    def _consume_ole(
        self, occurrence: dict[str, Any]
    ) -> tuple[int | None, dict[str, Any] | None, str | None]:
        ole_id = occurrence.get("ole_id")
        if not isinstance(ole_id, str) or not ole_id:
            return None, None, "ole_binding_missing"
        indices = self.oles_by_id.get(ole_id, [])
        if len(indices) != 1:
            return None, None, "ole_binding_ambiguous" if indices else "ole_binding_missing"
        index = indices[0]
        if index in self.used_oles:
            return None, None, "ole_binding_reused"
        ole = self.oles[index]
        preview_rel_ids = ole.get("preview_rel_ids")
        preview_media_paths = ole.get("preview_media_paths")
        source_rel_id = occurrence.get("source_rel_id")
        media_path = occurrence.get("media_path")
        if (
            not isinstance(preview_rel_ids, list)
            or not isinstance(source_rel_id, str)
            or source_rel_id not in preview_rel_ids
        ):
            return None, None, "ole_preview_relationship_unbound"
        if (
            not isinstance(preview_media_paths, list)
            or not isinstance(media_path, str)
            or media_path not in preview_media_paths
        ):
            return None, None, "ole_preview_media_unbound"
        self.used_oles.add(index)
        return index, ole, None

    def _media_event(self, occurrence: dict[str, Any] | None, *, error: str | None = None) -> None:
        if occurrence is None:
            self.events.append({"type": "invalid_media", "reason": error or "unbound_media"})
            return
        kind = occurrence.get("kind")
        if kind not in _MEDIA_KINDS:
            self.events.append(
                {
                    "type": "invalid_media",
                    "reason": "unknown_media_kind",
                    "provenance": self._provenance(occurrence),
                }
            )
            return
        ole = None
        native_status = None
        native_reason = None
        binding_error = error
        if kind == "ole_preview":
            _, ole, binding_error = self._consume_ole(occurrence)
            native_status, native, native_reason = self._native(ole)
        else:
            if occurrence.get("ole_id"):
                binding_error = "non_ole_media_has_ole_binding"
            native = None
        signature, signature_error = self._image_signature(occurrence)
        self.events.append(
            {
                "type": "media",
                "kind": kind,
                "occurrence": occurrence,
                "ole": ole,
                "native": native,
                "native_status": native_status,
                "native_reason": native_reason,
                "signature": signature,
                "signature_error": signature_error,
                "binding_error": binding_error,
                "provenance": self._provenance(occurrence),
            }
        )

    def _consume_image_path(self, source: str | None) -> None:
        if not isinstance(source, str) or not source:
            self._media_event(None, error="image_src_missing")
            return
        candidates = self.media_by_path.get(source)
        if not candidates:
            self._media_event(None, error="image_occurrence_unbound")
            return
        index = candidates.popleft()
        self.used_media.add(index)
        self._media_event(self.media[index])

    def _consume_omml(self) -> dict[str, Any]:
        if not self.omml_queue:
            self.issues.append({"code": "omml_occurrence_missing"})
            return {"canonical": None, "text": "", "error": "omml_occurrence_missing"}
        index = self.omml_queue.popleft()
        occurrence = self.omml[index]
        source_xml = occurrence.get("source_xml")
        try:
            if not isinstance(source_xml, str) or not source_xml:
                raise ValueError("source_xml_missing")
            root = ElementTree.fromstring(source_xml)
            canonical = _canonical_xml(root)
        except (ElementTree.ParseError, ValueError, TypeError) as exc:
            canonical = None
            reason = str(exc) or "invalid_source_xml"
            self.issues.append({"code": "omml_source_invalid", "reason": reason})
        else:
            reason = None
        return {"canonical": canonical, "text": str(occurrence.get("text", "")), "error": reason}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if self.math_capture is not None:
            self.math_capture["tokens"].append(("start", tag, tuple(sorted(attrs))))
            self.math_depth += 1
            return
        if self.unsupported_span_capture is not None:
            self.unsupported_span_capture["tokens"].append(("start", tag, tuple(sorted(attrs))))
            self.math_depth += 1
            return
        if tag == "math":
            self.math_capture = self._consume_omml()
            self.math_capture["tokens"] = [("start", tag, tuple(sorted(attrs)))]
            self.math_depth = 1
            return
        if tag == "span" and "unsupported-omml" in (attr_map.get("class") or "").split():
            item = self._consume_omml()
            item["tokens"] = [("unsupported_span", tuple(sorted(attrs)))]
            self.unsupported_span_capture = item
            self.math_depth = 1
            return
        if self.span_text is not None:
            self.span_text.append(f"<{tag}>")
            return
        if tag == "img":
            unknown_attrs = set(attr_map) - {"src", "alt", "width", "height"}
            if unknown_attrs:
                self.issues.append({"code": "unknown_image_attribute"})
                self.events.append({"type": "invalid_media", "reason": "unknown_image_attribute"})
            else:
                self._consume_image_path(attr_map.get("src"))
            return
        if tag == "span":
            self.span_text = []
            return
        if tag in _TABLE_TAGS:
            if tag in {"td", "th"}:
                unknown_attrs = set(attr_map) - {"colspan", "rowspan"}
                if unknown_attrs:
                    self.issues.append({"code": "unknown_table_attribute"})
                cells = []
                for name in ("colspan", "rowspan"):
                    value = attr_map.get(name)
                    if value is not None:
                        if not value.isdigit() or int(value) < 1:
                            self.issues.append({"code": "invalid_table_span"})
                            value = "invalid"
                        cells.append((name, value))
                self.events.append(
                    {"type": "table", "action": "start", "tag": tag, "attrs": tuple(cells)}
                )
            else:
                if attr_map:
                    self.issues.append({"code": "unknown_table_attribute"})
                self.events.append({"type": "table", "action": "start", "tag": tag, "attrs": ()})
            return
        if tag == "br":
            self.events.append({"type": "break"})
            return
        if tag == "p":
            if attr_map:
                self.issues.append({"code": "unknown_paragraph_attribute"})
            return
        # Source HTML currently emits no other display structures. Preserve the
        # unexpected tag as a blocker instead of silently dropping its contents.
        self.events.append({"type": "unknown_html", "tag": tag, "attrs": tuple(sorted(attrs))})
        self.issues.append({"code": "unknown_html_tag", "tag": tag})

    def handle_endtag(self, tag: str) -> None:
        if self.math_capture is not None:
            self.math_capture["tokens"].append(("end", tag))
            self.math_depth -= 1
            if self.math_depth == 0:
                self.events.append({"type": "math", **self.math_capture})
                self.math_capture = None
            return
        if self.unsupported_span_capture is not None:
            self.unsupported_span_capture["tokens"].append(("end", tag))
            self.math_depth -= 1
            if self.math_depth == 0:
                self.events.append({"type": "math", **self.unsupported_span_capture})
                self.unsupported_span_capture = None
            return
        if self.span_text is not None and tag == "span":
            contents = "".join(self.span_text)
            self.span_text = None
            if "嵌入公式预览缺失" in contents:
                self._emit_unpreviewed_oles()
            elif "图片预览缺失" in contents:
                self.events.append({"type": "invalid_media", "reason": "image_preview_missing"})
            else:
                self.events.append({"type": "text", "text": contents})
            return
        if tag in _TABLE_TAGS:
            self.events.append({"type": "table", "action": "end", "tag": tag, "attrs": ()})

    def _emit_unpreviewed_oles(self) -> None:
        for index, ole in enumerate(self.oles):
            if index in self.used_oles:
                continue
            self.used_oles.add(index)
            status, native, reason = self._native(ole)
            self.events.append(
                {
                    "type": "media",
                    "kind": "ole_preview",
                    "occurrence": None,
                    "ole": ole,
                    "native": native,
                    "native_status": status,
                    "native_reason": reason,
                    "signature": None,
                    "signature_error": "ole_preview_missing",
                    "binding_error": None,
                    "provenance": {
                        "document_id": self.block.get("document_id"),
                        "paragraph_index": self.block.get("paragraph_index"),
                        "ole_id": ole.get("ole_id"),
                        "source_part": ole.get("source_part"),
                    },
                }
            )

    def handle_data(self, data: str) -> None:
        if self.math_capture is not None or self.unsupported_span_capture is not None:
            target = self.math_capture or self.unsupported_span_capture
            target["tokens"].append(("text", _unicode_fold(data)))
            return
        if self.span_text is not None:
            self.span_text.append(data)
            return
        if data:
            self.events.append({"type": "text", "text": data})

    def handle_comment(self, data: str) -> None:
        self.events.append({"type": "unknown_html", "tag": "comment", "attrs": ()})
        self.issues.append({"code": "unknown_html_comment"})

    def finish(self) -> list[dict[str, Any]]:
        self.close()
        if self.math_capture is not None or self.unsupported_span_capture is not None:
            self.events.append({"type": "invalid_math", "reason": "unterminated_math_html"})
            self.issues.append({"code": "unterminated_math_html"})
        if self.span_text is not None:
            self.events.append({"type": "unknown_html", "tag": "unterminated_span", "attrs": ()})
            self.issues.append({"code": "unterminated_span"})

        # Every media/math occurrence must be represented in the visible block.
        for index, occurrence in enumerate(self.media):
            if index in self.used_media:
                continue
            if occurrence.get("kind") == "ole_preview" and occurrence.get("rendered") is not True:
                ole_id = occurrence.get("ole_id")
                if isinstance(ole_id, str) and ole_id in self.oles_by_id:
                    ole_index = self.oles_by_id[ole_id][0]
                    if ole_index not in self.used_oles:
                        self.used_media.add(index)
                        _, ole, bind_error = self._consume_ole(occurrence)
                        status, native, reason = self._native(ole)
                        self.events.append(
                            {
                                "type": "media",
                                "kind": "ole_preview",
                                "occurrence": occurrence,
                                "ole": ole,
                                "native": native,
                                "native_status": status,
                                "native_reason": reason,
                                "signature": None,
                                "signature_error": "ole_preview_missing",
                                "binding_error": bind_error,
                                "provenance": self._provenance(occurrence),
                            }
                        )
                        continue
            self.events.append(
                {
                    "type": "invalid_media",
                    "reason": "unrepresented_media_occurrence",
                    "provenance": self._provenance(occurrence),
                }
            )
            self.issues.append({"code": "unrepresented_media_occurrence"})

        for index, occurrence in enumerate(self.oles):
            if index not in self.used_oles:
                self.events.append(
                    {
                        "type": "invalid_media",
                        "reason": "unrepresented_ole_occurrence",
                        "provenance": {
                            "document_id": self.block.get("document_id"),
                            "paragraph_index": self.block.get("paragraph_index"),
                            "ole_id": occurrence.get("ole_id"),
                            "source_part": occurrence.get("source_part"),
                        },
                    }
                )
                self.issues.append({"code": "unrepresented_ole_occurrence"})
        if self.omml_queue:
            self.events.append({"type": "invalid_math", "reason": "unrepresented_omml_occurrence"})
            self.issues.append({"code": "unrepresented_omml_occurrence"})
        return self.events


def _unicode_fold(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    return "".join(
        chr(ord(char) - 0xFEE0) if "\uff01" <= char <= "\uff5e" else char for char in value
    )


def _normalized_text(value: str, *, question_prefix: bool = False) -> str:
    value = _unicode_fold(value)
    if question_prefix:
        value = _QUESTION_PREFIX.sub("", value, count=1)
    return "".join(char for char in value if not char.isspace())


def _canonical_xml(element: ElementTree.Element) -> tuple[Any, ...]:
    return (
        element.tag,
        tuple(sorted((key, _unicode_fold(value)) for key, value in element.attrib.items())),
        _unicode_fold(element.text or ""),
        tuple((_canonical_xml(child), _unicode_fold(child.tail or "")) for child in element),
    )


def _prepare_blocks(
    blocks: list[dict[str, Any]], root: Path, image_cache: dict[Path, tuple[tuple[int, int], bytes]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]], str]:
    events: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    raw_text: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            events.append({"type": "unknown_block", "reason": "block_not_object"})
            issues.append({"code": "invalid_block"})
            continue
        block = dict(block)
        block["_asset_root"] = root
        markup = block.get("html")
        if isinstance(markup, str):
            parser = _BlockEventsParser(block, image_cache)
            try:
                parser.feed(markup)
                block_events = parser.finish()
            except Exception as exc:
                block_events = [{"type": "unknown_block", "reason": "html_parse_failed"}]
                parser.issues.append({"code": "html_parse_failed", "reason": type(exc).__name__})
            events.extend(block_events)
            issues.extend(parser.issues)
            parsed_text = "".join(
                event.get("text", "")
                if event.get("type") == "text"
                else event.get("text", "")
                if event.get("type") == "math"
                else ""
                for event in block_events
            )
            source_text = block.get("text", "")
            if isinstance(source_text, str):
                if _normalized_text(parsed_text) != _normalized_text(source_text):
                    issues.append({"code": "html_text_projection_mismatch"})
                    events.append(
                        {"type": "unknown_block", "reason": "html_text_projection_mismatch"}
                    )
                raw_text.append(source_text)
            else:
                issues.append({"code": "block_text_invalid"})
                events.append({"type": "unknown_block", "reason": "block_text_invalid"})
        else:
            text = block.get("text", "")
            if isinstance(text, str):
                raw_text.append(text)
                events.append({"type": "text", "text": text})
            if (
                block.get("media_occurrences")
                or block.get("ole_occurrences")
                or block.get("omml_occurrences")
            ):
                events.append(
                    {"type": "unknown_block", "reason": "markup_missing_for_structured_content"}
                )
                issues.append({"code": "markup_missing_for_structured_content"})
    return events, issues, "".join(raw_text)


def _normalized_events(events: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    merged: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "text":
            if merged and merged[-1].get("type") == "text":
                merged[-1]["text"] += event.get("text", "")
            else:
                merged.append({"type": "text", "text": event.get("text", "")})
        else:
            merged.append(event)
    normalized: list[tuple[Any, ...]] = []
    first_text = True
    for event in merged:
        kind = event.get("type")
        if kind == "text":
            text = _normalized_text(event.get("text", ""), question_prefix=first_text)
            first_text = False
            if text:
                normalized.append(("text", text))
        elif kind == "math":
            normalized.append(("math", event.get("canonical"), event.get("tokens")))
        elif kind == "media":
            normalized.append(("media", event.get("kind")))
        elif kind == "table":
            normalized.append(
                ("table", event.get("action"), event.get("tag"), event.get("attrs", ()))
            )
        elif kind == "break":
            normalized.append(("break",))
        else:
            normalized.append(("unknown", kind, event.get("reason"), event.get("tag")))
    return normalized


def _text_projection(value: str) -> str:
    return _normalized_text(value, question_prefix=True)


def _image_count(blocks: list[dict[str, Any]]) -> int:
    return sum(
        len(block.get("media_occurrences", [])) for block in blocks if isinstance(block, dict)
    )


def _media_provenance(event: dict[str, Any] | None) -> dict[str, Any] | None:
    return event.get("provenance") if event else None


def _compare_media(source: dict[str, Any], analysis: dict[str, Any], index: int) -> dict[str, Any]:
    kind_a, kind_b = source.get("kind"), analysis.get("kind")
    base = {
        "index": index,
        "kind": kind_a if kind_a == kind_b else {"source": kind_a, "analysis": kind_b},
        "source": _media_provenance(source),
        "analysis": _media_provenance(analysis),
    }
    if kind_a != kind_b or kind_a not in _MEDIA_KINDS:
        return {
            **base,
            "method": "unmatched",
            "matched": False,
            "reason_code": "media_kind_mismatch",
        }
    if source.get("binding_error") or analysis.get("binding_error"):
        return {
            **base,
            "method": "unmatched",
            "matched": False,
            "reason_code": source.get("binding_error") or analysis.get("binding_error"),
        }
    if (
        source.get("occurrence") is None
        or analysis.get("occurrence") is None
        or source.get("signature") is None
        or analysis.get("signature") is None
        or source.get("signature_error")
        or analysis.get("signature_error")
    ):
        return {
            **base,
            "method": "unmatched",
            "matched": False,
            "reason_code": source.get("signature_error")
            or analysis.get("signature_error")
            or "preview_unavailable",
        }
    if kind_a == "ole_preview":
        state_a, state_b = source.get("native_status"), analysis.get("native_status")
        native_a, native_b = source.get("native"), analysis.get("native")
        if state_a == "declared_invalid" or state_b == "declared_invalid":
            return {
                **base,
                "method": "unmatched",
                "matched": False,
                "reason_code": "native_metadata_malformed",
            }
        if state_a == "valid" and state_b == "valid":
            same = (
                native_a["payload_sha256"].lower() == native_b["payload_sha256"].lower()
                and native_a["payload_size"] == native_b["payload_size"]
                and native_a["mtef_version"] == native_b["mtef_version"]
            )
            return {
                **base,
                "method": "native_mtef_exact",
                "matched": same,
                "reason_code": None if same else "native_payload_mismatch",
                "native_evidence": {
                    "source": {
                        k: native_a.get(k)
                        for k in (
                            "method",
                            "payload_sha256",
                            "payload_size",
                            "mtef_version",
                            "ole_sha256",
                        )
                    },
                    "analysis": {
                        k: native_b.get(k)
                        for k in (
                            "method",
                            "payload_sha256",
                            "payload_size",
                            "mtef_version",
                            "ole_sha256",
                        )
                    },
                    "source_preview": {
                        "size": list(source["signature"][0]) if source.get("signature") else None,
                        "rgba_sha256": hashlib.sha256(source["signature"][1]).hexdigest()
                        if source.get("signature")
                        else None,
                        "error": source.get("signature_error"),
                    },
                    "analysis_preview": {
                        "size": list(analysis["signature"][0])
                        if analysis.get("signature")
                        else None,
                        "rgba_sha256": hashlib.sha256(analysis["signature"][1]).hexdigest()
                        if analysis.get("signature")
                        else None,
                        "error": analysis.get("signature_error"),
                    },
                    "preview_pixels_match": (
                        source["signature"] == analysis["signature"]
                        if source.get("signature") is not None
                        and analysis.get("signature") is not None
                        else None
                    ),
                },
            }
    sig_a, sig_b = source.get("signature"), analysis.get("signature")
    same = sig_a is not None and sig_b is not None and sig_a == sig_b
    reasons = [
        x
        for x in (
            source.get("native_reason"),
            analysis.get("native_reason"),
            source.get("signature_error"),
            analysis.get("signature_error"),
        )
        if x
    ]
    return {
        **base,
        "method": "exact_rgba",
        "matched": same,
        "reason_code": None if same else (reasons[0] if reasons else "rgba_mismatch"),
        "fallback_reason": reasons or ["native_evidence_unavailable"],
        "source_size": list(sig_a[0]) if sig_a is not None else None,
        "analysis_size": list(sig_b[0]) if sig_b is not None else None,
        "source_rgba_sha256": hashlib.sha256(sig_a[1]).hexdigest() if sig_a is not None else None,
        "analysis_rgba_sha256": hashlib.sha256(sig_b[1]).hexdigest() if sig_b is not None else None,
    }


def compare_association(
    source_blocks: list[dict], analysis_blocks: list[dict], root: Path
) -> tuple[str, dict]:
    """Compare a paired question by ordered text, table, math, and media evidence."""
    if not isinstance(source_blocks, list) or not isinstance(analysis_blocks, list):
        raise TypeError("source_blocks and analysis_blocks must be lists")
    source_events, source_issues, source_raw_text = _prepare_blocks(source_blocks, Path(root), {})
    analysis_events, analysis_issues, analysis_raw_text = _prepare_blocks(
        analysis_blocks, Path(root), {}
    )
    source_projection = _text_projection(source_raw_text)
    analysis_projection = _text_projection(analysis_raw_text)
    text_match = bool(source_projection) and source_projection == analysis_projection
    source_signature = _normalized_events(source_events)
    analysis_signature = _normalized_events(analysis_events)
    structure_match = source_signature == analysis_signature

    source_media = [e for e in source_events if e.get("type") == "media"]
    analysis_media = [e for e in analysis_events if e.get("type") == "media"]
    media_evidence: list[dict[str, Any]] = []
    if len(source_media) != len(analysis_media):
        media_evidence.append(
            {
                "index": min(len(source_media), len(analysis_media)) + 1,
                "method": "unmatched",
                "matched": False,
                "reason_code": "media_count_mismatch",
                "source_count": len(source_media),
                "analysis_count": len(analysis_media),
            }
        )
    for index, (source, analysis) in enumerate(zip(source_media, analysis_media), 1):
        media_evidence.append(_compare_media(source, analysis, index))

    source_omml = [e for e in source_events if e.get("type") == "math"]
    analysis_omml = [e for e in analysis_events if e.get("type") == "math"]
    if len(source_omml) != len(analysis_omml):
        omml_structure_match: bool | None = False
    elif any(e.get("canonical") is None for e in source_omml + analysis_omml):
        omml_structure_match = None
    else:
        omml_structure_match = all(
            a.get("canonical") == b.get("canonical") and a.get("tokens") == b.get("tokens")
            for a, b in zip(source_omml, analysis_omml)
        )

    media_identity_match = (
        len(source_media) == len(analysis_media)
        and all(item.get("matched") is True for item in media_evidence)
        and not any(e.get("type") == "invalid_media" for e in source_events + analysis_events)
    )
    if len(source_media) != len(analysis_media):
        image_pixels_match: bool | None = False
    elif not source_media:
        image_pixels_match = True
    elif any(item.get("signature") is None for item in source_media + analysis_media):
        image_pixels_match = None
    else:
        image_pixels_match = all(
            source["signature"] == analysis["signature"]
            for source, analysis in zip(source_media, analysis_media)
        )
    issue_codes = {item.get("code") for item in source_issues + analysis_issues}
    has_blockers = bool(issue_codes) or any(
        e.get("type") in {"invalid_media", "invalid_math", "unknown_html", "unknown_block"}
        for e in source_events + analysis_events
    )
    confirmed = bool(
        text_match
        and structure_match
        and media_identity_match
        and omml_structure_match is True
        and not has_blockers
        and (source_projection or media_evidence)
    )
    evidence = {
        "text_match": text_match,
        "source_image_count": _image_count(source_blocks),
        "analysis_image_count": _image_count(analysis_blocks),
        "image_pixels_match": image_pixels_match,
        "media_identity_match": media_identity_match,
        "omml_structure_match": omml_structure_match,
        "structure_match": structure_match,
        "media_evidence": media_evidence,
        "comparison_method": "source_association_v3",
        "issues": sorted(issue_codes),
    }
    return ("confirmed" if confirmed else "unverified"), evidence
