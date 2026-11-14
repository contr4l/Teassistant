"""Render immutable source-bank questions as complete semantic-model inputs.

The renderer does not call a model or modify the source bank. It emits a fresh,
content-addressed manifest and lossless-size PNG tiles for each selected question.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from lxml import html as lxml_html
from PIL import Image

from app.tool_client import ToolClientError, render_screenshot, tools_enabled

_MATH_TAGS = {
    "math",
    "mrow",
    "mi",
    "mn",
    "mo",
    "mtext",
    "mfrac",
    "msqrt",
    "mroot",
    "msup",
    "msub",
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
_MATH_ATTR_VALUES = {
    "xmlns": {"http://www.w3.org/1998/Math/MathML"},
    "mathvariant": {"normal"},
    "fence": {"true", "false"},
    "stretchy": {"true", "false"},
    "accent": {"true", "false"},
    "accentunder": {"true", "false"},
}
_HTML_TAGS = {
    "span",
    "p",
    "br",
    "img",
    "strong",
    "em",
    "b",
    "i",
    "sup",
    "sub",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
}
_HTML_ATTRS = {"src", "alt", "width", "height", "colspan", "rowspan"}
_NUMBER = re.compile(r"^[1-9][0-9]{0,5}$")
_IMAGE_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
_TILE_HEIGHT = 2400
_TILE_OVERLAP = 80


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _inside(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw:
        raise ValueError("media path must be a relative local path")
    relative = Path(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("media path escapes the source bank")
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_file() or not candidate.is_relative_to(root):
        raise ValueError("media file is missing or escapes the source bank")
    return candidate


def safe_fragment(value: str, bank_root: Path) -> str:
    """Sanitize importer markup and replace bank-relative images with local file URIs."""
    if not isinstance(value, str):
        raise TypeError("source html fragment must be text")
    root = bank_root.resolve(strict=True)
    wrapper = lxml_html.fragment_fromstring(value or "", create_parent="div")
    for node in wrapper.iterdescendants():
        tag = str(node.tag).lower() if isinstance(node.tag, str) else ""
        if tag in _MATH_TAGS:
            for name, content in node.attrib.items():
                key = name.rsplit("}", 1)[-1].lower()
                if key not in _MATH_ATTRS.get(tag, set()) or content not in _MATH_ATTR_VALUES[key]:
                    raise ValueError("unsupported mathematical markup attribute")
            continue
        if tag not in _HTML_TAGS:
            raise ValueError("unsupported markup in source fragment")
        for name in node.attrib:
            if name.lower() not in _HTML_ATTRS:
                raise ValueError("unsupported attribute in source fragment")
        if tag == "img":
            src = node.get("src", "")
            if (
                not src.startswith("media/")
                or any(char in src for char in ("%", "?", "#", "\x00"))
                or any(ord(char) < 32 or ord(char) == 127 for char in src)
            ):
                raise ValueError("source image must be a local bank media path")
            media = _inside(root, src)
            try:
                with Image.open(media) as image:
                    mime = _IMAGE_MIME.get(image.format or "")
                    if mime is None:
                        raise ValueError("source image must be PNG, JPEG, WebP, or GIF")
                    image.verify()
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError("source image is invalid or unsafe to decode") from exc
            encoded = base64.b64encode(media.read_bytes()).decode("ascii")
            node.set("src", f"data:{mime};base64,{encoded}")
            for dim in ("width", "height"):
                raw_dim = node.get(dim)
                if raw_dim is not None and not _NUMBER.fullmatch(raw_dim):
                    raise ValueError("image dimensions must be positive integer pixels")
        for name in ("colspan", "rowspan"):
            raw_span = node.get(name)
            if raw_span is not None and not _NUMBER.fullmatch(raw_span):
                raise ValueError("table spans must be positive integers")
    return "".join(lxml_html.tostring(child, encoding="unicode") for child in wrapper)


def _section_text(blocks: list[dict]) -> str:
    return "\n".join(str(block.get("text", "")) for block in blocks if block.get("text"))


def _prepare_question(question: dict, bank_root: Path) -> tuple[dict, str]:
    association = question.get("association_status", "unknown")
    reliable = association == "confirmed"
    selected = {"stem_blocks": question.get("stem_blocks", [])}
    selected["answer_blocks"] = question.get("answer_blocks", []) if reliable else []
    selected["analysis_blocks"] = question.get("analysis_blocks", []) if reliable else []
    for section, blocks in selected.items():
        if not isinstance(blocks, list):
            raise ValueError(f"{section} must be a list")
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError(f"{section} contains an invalid block")
            if not isinstance(block.get("html", ""), str) or not isinstance(
                block.get("text", ""), str
            ):
                raise ValueError(f"{section} block text and html must be strings")
    rendered = []
    text_sections = []
    for section, title in (
        ("stem_blocks", "题目"),
        ("answer_blocks", "答案"),
        ("analysis_blocks", "解析"),
    ):
        blocks = selected[section]
        section_text = _section_text(blocks)
        if section_text:
            text_sections.append(f"【{title}】\n{section_text}")
        fragments = "".join(
            f'<div class="source-block">{safe_fragment(block.get("html", ""), bank_root)}</div>'
            for block in blocks
        )
        if section == "stem_blocks":
            rendered.append(f"<section><h2>{title}</h2>{fragments}</section>")
        else:
            rendered.append(
                f"<section><h2>{title}</h2>{fragments or '<p>未提供可确认内容</p>'}</section>"
            )
    data = {
        "question_id": question.get("id"),
        "source_hash": _sha256_bytes(_canonical_json(question)),
        "association_status": association,
        "reliable_solution_used": reliable,
        "text": "\n\n".join(text_sections),
    }
    return data, "".join(rendered)


def _html_document(body: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
* {{ box-sizing:border-box }}
html,body {{ margin:0; padding:0; background:#fff; color:#111; font-family:system-ui,"PingFang SC","Microsoft YaHei",sans-serif }}
body {{ padding:28px 32px; width:max-content; min-width:1100px; font-size:20px; line-height:1.75 }}
main {{ width:1036px }}
h2 {{ font-size:22px; margin:22px 0 8px; border-bottom:1px solid #999 }}
section:first-child h2 {{ margin-top:0 }}
p {{ margin:6px 0; white-space:normal }}
img {{ display:inline-block; vertical-align:middle; max-width:none; height:auto }}
table {{ border-collapse:collapse; margin:10px 0; max-width:none }}
td,th {{ border:1px solid #777; padding:4px 8px; vertical-align:top }}
math {{ font-family:serif }}
</style></head><body><main>{body}</main></body></html>"""


def _tiles(png_data: bytes, target_dir: Path, qindex: int) -> list[dict]:
    from io import BytesIO

    with Image.open(BytesIO(png_data)) as image:
        image.load()
        if image.format != "PNG" or image.width < 1 or image.height < 1:
            raise ValueError("browser screenshot is not a valid PNG")
        width, height = image.size
        records = []
        y = 0
        tile_number = 1
        while y < height:
            end = min(y + _TILE_HEIGHT, height)
            tile = image.crop((0, y, width, end))
            path = target_dir / f"q{qindex:03d}-tile{tile_number:03d}.png"
            tile.save(path, format="PNG", optimize=False)
            with Image.open(path) as check:
                check.verify()
            raw = path.read_bytes()
            records.append(
                {
                    "path": path.relative_to(target_dir.parent).as_posix(),
                    "sha256": _sha256_bytes(raw),
                    "width": width,
                    "height": end - y,
                    "source_y_start": y,
                    "source_y_end": end,
                    "source_width": width,
                    "source_height": height,
                }
            )
            if end == height:
                break
            y = end - _TILE_OVERLAP
            tile_number += 1
        if sum(record["height"] for record in records) < height:
            raise ValueError("image tiling failed to preserve the complete page")
        return records


@contextmanager
def _browser_page(chromium_executable: str | None) -> Iterator[object]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required to render semantic inputs") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chromium_executable,
        )
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 1200}, device_scale_factor=1)
            yield page
        finally:
            browser.close()


def _render_page(page: object, html_page: str, *, output_dir: Path, qindex: int) -> list[dict]:
    page.set_content(html_page, wait_until="load", timeout=30000)
    page.evaluate("() => document.fonts.ready.then(() => true)")
    image_state = page.locator("img").evaluate_all(
        "images => images.map(img => ({ok: img.complete && img.naturalWidth > 0, width: img.naturalWidth, height: img.naturalHeight}))"
    )
    if any(not item["ok"] for item in image_state):
        raise ValueError("one or more source images failed to load")
    size = page.evaluate("""() => ({
        width: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
        height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
        overflow: [...document.querySelectorAll('main,section,p,table')].some(el => el.scrollWidth > el.clientWidth && getComputedStyle(el).overflowX !== 'visible')
    })""")
    if size["width"] <= 0 or size["height"] <= 0 or size["overflow"]:
        raise ValueError("rendered question has invalid or clipped layout")
    if size["width"] != 1100:
        page.set_viewport_size({"width": size["width"], "height": 1200})
    screenshot = page.screenshot(full_page=True, type="png", animations="disabled")
    from io import BytesIO

    with Image.open(BytesIO(screenshot)) as image:
        if image.width < size["width"] or image.height < size["height"]:
            raise ValueError("screenshot dimensions do not cover the complete rendered question")
    return _tiles(screenshot, output_dir, qindex)


def _render_remote_page(html_page: str, *, output_dir: Path, qindex: int) -> list[dict]:
    try:
        screenshot, width, height = render_screenshot(html_page)
    except ToolClientError as exc:
        raise ValueError(f"remote browser screenshot failed: {exc}") from exc
    from io import BytesIO

    try:
        with Image.open(BytesIO(screenshot)) as image:
            image.load()
            if image.format != "PNG" or image.size != (width, height):
                raise ValueError("remote screenshot dimensions do not match the PNG")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("remote browser screenshot is not a valid PNG") from exc
    return _tiles(screenshot, output_dir, qindex)


def render_inputs(
    bank_dir: Path,
    output_dir: Path,
    question_ids: list[str] | None = None,
    *,
    chromium_executable: str | None = None,
) -> dict:
    """Render source V3 questions into fresh PNG tiles and write ``inputs.json``.

    With no question_ids, the source must contain the fixed 200-question batch.
    A selected subset is useful for pilot runs and tests; it does not change the bank.
    """
    source_root = Path(bank_dir).resolve(strict=True)
    if not source_root.is_dir():
        raise ValueError("bank_dir must be a directory")
    bank_path = (source_root / "bank.json").resolve(strict=True)
    if not bank_path.is_file() or not bank_path.is_relative_to(source_root):
        raise ValueError("bank.json is missing from source bank")
    bank_bytes = bank_path.read_bytes()
    bank = json.loads(bank_bytes)
    questions = bank.get("questions")
    if not isinstance(questions, list):
        raise ValueError("source bank questions must be a list")
    by_id = {}
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str):
            raise ValueError("source bank contains a question without a valid id")
        if question["id"] in by_id:
            raise ValueError("source bank contains duplicate question ids")
        by_id[question["id"]] = question
    if question_ids is None:
        if len(by_id) != 200:
            raise ValueError("full semantic input rendering requires exactly 200 source questions")
        selected_ids = list(by_id)
    else:
        if not question_ids or len(set(question_ids)) != len(question_ids):
            raise ValueError("question_ids must be a non-empty unique list")
        missing = [qid for qid in question_ids if qid not in by_id]
        if missing:
            raise ValueError(f"unknown question ids: {missing}")
        selected_ids = list(question_ids)

    output_path = Path(output_dir).expanduser().absolute()
    if output_path.exists():
        raise FileExistsError("output_dir must be fresh; existing outputs are never overwritten")
    resolved_output = output_path.resolve(strict=False)
    if source_root == resolved_output or source_root in resolved_output.parents:
        raise ValueError("output_dir must be outside the source bank")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.stage-", dir=output_path.parent))
    try:
        image_root = stage / "images"
        image_root.mkdir()
        records = []
        if tools_enabled():
            for index, qid in enumerate(selected_ids, 1):
                question = by_id[qid]
                prepared, body = _prepare_question(question, source_root)
                tiles = _render_remote_page(
                    _html_document(body), output_dir=image_root, qindex=index
                )
                records.append({**prepared, "images": tiles})
        else:
            with _browser_page(chromium_executable) as page:
                for index, qid in enumerate(selected_ids, 1):
                    question = by_id[qid]
                    prepared, body = _prepare_question(question, source_root)
                    tiles = _render_page(
                        page,
                        _html_document(body),
                        output_dir=image_root,
                        qindex=index,
                    )
                    records.append({**prepared, "images": tiles})
        manifest = {
            "source_bank_sha256": _sha256_bytes(bank_bytes),
            "renderer_version": "source-v3-renderer-v1",
            "question_count": len(records),
            "questions": records,
        }
        (stage / "inputs.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        stage.rename(output_path)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
