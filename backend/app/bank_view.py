"""Portable, local-only question library preview; content is emitted by the OOXML importer."""

import html
import json
from pathlib import Path
from urllib.parse import quote

MATHML_TAGS = {
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
MATHML_ALLOWED_ATTRIBUTES = {
    "math": {"xmlns"},
    "mi": {"mathvariant"},
    "mn": {"mathvariant"},
    "mo": {"mathvariant", "fence", "stretchy"},
    "mtext": {"mathvariant"},
    "mover": {"accent"},
    "munder": {"accentunder"},
}
MATHML_ATTRIBUTES = {
    "mathvariant": {"normal"},
    "stretchy": {"true", "false"},
    "fence": {"true", "false"},
    "accent": {"true", "false"},
    "accentunder": {"true", "false"},
    "xmlns": {"http://www.w3.org/1998/Math/MathML"},
}


def _safe_fragment(value: str) -> str:
    """Allow only importer-generated local media markup and escaped text."""
    from lxml import html as lh

    wrapper = lh.fragment_fromstring(value or "", create_parent="div")
    for node in wrapper.iterdescendants():
        if node.tag in MATHML_TAGS:
            if any(
                k not in MATHML_ALLOWED_ATTRIBUTES.get(node.tag, set())
                or v not in MATHML_ATTRIBUTES[k]
                for k, v in node.attrib.items()
            ):
                raise ValueError("unsupported mathematical markup attribute")
            continue
        if node.tag not in {
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
            "tr",
            "td",
            "th",
            "tbody",
        }:
            raise ValueError("unsupported markup in source fragment")
        for name in list(node.attrib):
            if name not in {
                "src",
                "alt",
                "width",
                "height",
                "style",
                "class",
                "colspan",
                "rowspan",
                "loading",
            }:
                raise ValueError("unsupported attribute in source fragment")
        if node.tag == "img":
            src = node.get("src", "")
            if (
                not src.startswith("media/")
                or ".." in Path(src).parts
                or any(c in src for c in (":", "\\", "%", "?", "#"))
                or any(ord(c) < 32 or ord(c) == 127 for c in src)
            ):
                raise ValueError("source image must remain inside local media")
        if node.get("style") and any(
            token in node.get("style").lower() for token in ("url", "expression", "@import", "\\")
        ):
            raise ValueError("unsupported style in source fragment")
    return value


def _issue_message(issue) -> str:
    code = issue.get("code") if isinstance(issue, dict) else issue
    return {
        "missing_original_question": "缺少对应的原卷题目",
        "missing_analysis_question": "缺少对应的答案记录",
        "missing_answer": "没有完整可用的答案",
        "association_unverified": "题目与答案来源的对应关系尚待自动核验",
        "unsupported_omml": "包含暂不支持的 Word 公式，请查看原文件",
        "missing_media_relationship": "缺少必要的公式预览或配图",
        "ole_preview_missing": "缺少数学公式预览",
        "missing_question_numbers": "题号存在缺口",
        "declared_count_mismatch": "识别题数与资料声明不一致",
    }.get(code, "资料处理存在异常，请查看原文件或处理记录")


def render_library(bank: dict, output_dir: Path) -> Path:
    """Build searchable static HTML; no network request or runtime service required."""
    esc = html.escape
    pairs = {p["id"]: p for p in bank["pairs"]}
    documents = {d["id"]: d for d in bank["documents"]}
    topics = sorted({t["label"] for q in bank["questions"] for t in q.get("topics", [])})

    def options(values):
        return "".join(f'<option value="{esc(v, quote=True)}">{esc(v)}</option>' for v in values)

    articles = []
    question_types = {
        "single_choice": "选择题",
        "multiple_choice": "多选题",
        "fill_blank": "填空题",
        "free_response": "解答题",
        "proof": "证明题",
        "true_false": "判断题",
        "unknown": "题型未确定",
    }
    for q in bank["questions"]:
        pair = pairs[q["pair_id"]]
        labels = sorted({t["label"] for t in q.get("topics", [])})
        searchable = " ".join(
            [
                pair["title"],
                str(q["number"]),
                q.get("question_type", ""),
                *labels,
                *(
                    b.get("text", "")
                    for key in ("stem_blocks", "answer_blocks", "analysis_blocks")
                    for b in q.get(key, [])
                ),
            ]
        )
        sections = []
        for key, title in [
            ("stem_blocks", "题目"),
            ("answer_blocks", "答案"),
            ("analysis_blocks", "解析"),
        ]:
            blocks = q.get(key, [])
            content = "".join(
                f'<div class="source-block">{_safe_fragment(b.get("html", ""))}</div>'
                for b in blocks
            )
            if key != "stem_blocks":
                empty_message = "暂无解析" if key == "analysis_blocks" else "未取得完整答案"
                content = f'<details><summary>{title}</summary><div class="source-content">{content or empty_message}</div></details>'
            else:
                content = f'<div class="source-content">{content}</div>'
            sections.append(content)
        source_links = []
        for key, title in [("original_document_id", "原卷"), ("analysis_document_id", "解析版")]:
            if pair.get(key) in documents:
                filename = documents[pair[key]]["filename"]
                source_links.append(f'<a href="sources/{quote(filename)}" download>{title}</a>')
        chips = (
            "".join(f'<span class="chip">{esc(t)}</span>' for t in labels)
            or '<span class="muted">暂无来源考点标签</span>'
        )
        issues = q.get("issues", [])
        warning = (
            f'<p class="warning">处理提示：{esc("；".join(dict.fromkeys(_issue_message(i) for i in issues)))}</p>'
            if issues
            else ""
        )
        association = {
            "confirmed": "题答来源已确认",
            "unverified": "题答来源待进一步自动核验",
        }.get(q.get("association_status"), "")
        association_issue_shown = any(
            isinstance(i, dict) and i.get("code") == "association_unverified" for i in issues
        )
        if association and not association_issue_shown:
            warning += f'<p class="muted">{association}</p>'
        attrs = f'data-search="{esc(searchable.casefold(), quote=True)}" data-pair="{esc(pair["title"], quote=True)}" data-topics="{esc(json.dumps(labels, ensure_ascii=False), quote=True)}"'
        articles.append(
            f'<article {attrs}><div class="question-head"><h2>第 {q["number"]} 题</h2><span>{esc(question_types.get(q.get("question_type"), q.get("question_type", "")))}</span></div><p class="source">{esc(pair["title"])}</p><div>{chips}</div>{warning}{"".join(sections)}<footer>{" · ".join(source_links)}</footer></article>'
        )
    bank_status = "本地题库 · 原卷与解析配对"
    status = bank.get("batch_status", "unknown")
    status_text = {
        "succeeded": "来源资料处理完成",
        "failed": "来源资料处理失败：请查看输入文件与处理记录",
    }.get(status, "来源记录（批次状态未提供）")
    if status == "partial":
        details = []
        for count, message in (
            (len(bank.get("failed_documents", [])), "个文件未能处理"),
            (
                sum(q.get("structure_status") == "processing_failed" for q in bank["questions"]),
                "题结构处理失败",
            ),
            (
                sum(q.get("association_status") == "unverified" for q in bank["questions"]),
                "题的题答关系待自动核验",
            ),
        ):
            if count:
                details.append(f"{count}{message}")
        status_text = "来源资料部分完成：" + ("；".join(details) or "请查看处理记录")
    status_banner = f'<p class="{"summary" if status == "succeeded" else "warning"}" data-batch-status="{esc(status, quote=True)}">{status_text}</p>'
    if bank.get("failed_documents"):
        names = "、".join(str(d.get("filename", "未命名文件")) for d in bank["failed_documents"])
        status_banner += f'<p class="warning">未能处理的文件：{esc(names)}</p>'
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>长沙九年级数学试题库</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f9;color:#1c2a3b;font-family:system-ui,"PingFang SC","Microsoft YaHei",sans-serif}}header,main{{max-width:1120px;margin:auto;padding:28px 24px}}header{{padding-bottom:12px}}h1{{font-size:30px;margin:8px 0}}.eyebrow{{font-size:13px;color:#456384}}.intro{{color:#526379;line-height:1.8}}.toolbar{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 12px}}input,select{{font:inherit;padding:12px;border:1px solid #cbd5df;border-radius:8px;background:white}}input{{flex:1;min-width:200px}}select{{max-width:340px}}.summary{{font-size:14px;color:#526379}}article{{background:white;border:1px solid #dce3eb;border-radius:12px;padding:22px;margin:20px 0}}.question-head{{display:flex;justify-content:space-between;align-items:center}}h2{{font-size:20px;margin:0}}.source{{font-size:13px;color:#64758b;line-height:1.6}}.chip{{display:inline-block;background:#edf3fb;color:#345c8b;padding:4px 9px;margin:3px;border-radius:5px;font-size:13px}}.muted{{color:#748194;font-size:13px}}.source-content{{line-height:2.1;overflow-wrap:anywhere;color:#182331;margin-top:14px}}.source-content img{{max-width:100%;height:auto;vertical-align:middle}}.source-content p{{margin:8px 0}}.source-content table{{border-collapse:collapse;margin:12px 0;max-width:100%}}.source-content td,.source-content th{{border:1px solid #bcc9d8;padding:4px 12px}}details{{border-top:1px solid #e5eaf0;margin-top:16px;padding-top:14px}}summary{{cursor:pointer;color:#315d91;font-weight:600}}footer{{margin-top:16px;font-size:13px}}a{{color:#315d91}}.warning{{color:#9a5a00;background:#fff6e5;padding:10px}}[hidden]{{display:none!important}}@media(max-width:600px){{header,main{{padding:18px 12px}}article{{padding:16px}}h1{{font-size:24px}}select{{width:100%;max-width:none}}}}
</style></head><body><header><div class="eyebrow">{bank_status}</div><h1>长沙九年级数学试题库</h1>{status_banner}<p class="intro">{len(bank["pairs"])} 套试卷 · {len(bank["questions"])} 道大题 · {len(topics)} 个来源考点索引。保留公式与图形，支持的 Word 公式使用数学排版；考点标签取自解析中的已有陈述。尚未进行 AI 难度评分，不能作为按难度自动组卷的最终题库。</p><div class="toolbar"><input id="search" placeholder="搜索文字、考点或题号" aria-label="搜索"><select id="pair"><option value="">全部试卷</option>{options(sorted(p["title"] for p in bank["pairs"]))}</select><select id="topic"><option value="">全部考点</option>{options(topics)}</select></div><p class="summary" id="count"></p></header><main>{"".join(articles)}<p id="empty" hidden>没有匹配的题目。</p></main><script>
const articles=[...document.querySelectorAll('article')],search=document.getElementById('search'),pair=document.getElementById('pair'),topic=document.getElementById('topic');
function filter(){{let shown=0;for(const a of articles){{let ok=(!search.value||a.dataset.search.includes(search.value.trim().toLowerCase()))&&(!pair.value||a.dataset.pair===pair.value)&&(!topic.value||JSON.parse(a.dataset.topics).includes(topic.value));a.hidden=!ok;if(ok)shown++;}}document.getElementById('count').textContent=`显示 ${{shown}} / ${{articles.length}} 道题`;document.getElementById('empty').hidden=shown!==0;}}
for(const e of [search,pair,topic])e.addEventListener('input',filter);filter();
</script></body></html>"""
    destination = output_dir / "index.html"
    destination.write_text(page, encoding="utf-8")
    return destination
