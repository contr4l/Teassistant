"""Build a private source-preserving DOCX bank, SQLite index and local browser."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from lxml import html as lh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bank_index import index_bank  # noqa: E402
from app.bank_view import render_library  # noqa: E402
from app.docx_ingest import DocxIngestError, build_source_bank  # noqa: E402


def add_exact_fingerprints(bank: dict, directory: Path) -> None:
    """Group normalized structured content, retaining every source occurrence."""
    groups: dict[str, list[str]] = {}
    structural_tags = {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
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
        "mtable",
        "mtr",
        "mtd",
        "menclose",
    }
    semantic_attributes = {"rowspan", "colspan", "mathvariant", "accent", "accentunder", "notation"}
    for question in bank["questions"]:
        tokens = []
        has_content = False
        first_text = True
        for block in question["stem_blocks"]:
            fragment = lh.fragment_fromstring(block["html"] or "", create_parent="div")
            block_tokens = []

            def add_text(value, *, strip_prefix=True):
                nonlocal first_text, has_content
                value = re.sub(r"\s+", "", value)
                if first_text and value and strip_prefix:
                    value = re.sub(r"^\d+[.．、)]", "", value)
                    first_text = False
                if value:
                    block_tokens.append(["text", value])
                    has_content = True

            def walk(node, in_math=False):
                nonlocal has_content
                in_math = in_math or node.tag == "math"
                structural = node.tag in structural_tags
                if structural:
                    block_tokens.append(
                        [
                            "open",
                            node.tag,
                            sorted(
                                (k, v) for k, v in node.attrib.items() if k in semantic_attributes
                            ),
                        ]
                    )
                if node.text:
                    add_text(node.text, strip_prefix=not in_math)
                for child in node:
                    if child.tag == "img":
                        media = (directory / child.get("src", "")).resolve(strict=True)
                        media.relative_to(directory.resolve())
                        block_tokens.append(
                            ["image", hashlib.sha256(media.read_bytes()).hexdigest()]
                        )
                        has_content = True
                    else:
                        walk(child, in_math)
                    if child.tail:
                        add_text(child.tail, strip_prefix=not in_math)
                if structural:
                    block_tokens.append(["close", node.tag])

            walk(fragment)
            for occurrence in block.get("omml_occurrences", []):
                if not occurrence.get("supported", False) and occurrence.get("source_xml"):
                    block_tokens.append(
                        [
                            "unsupported-math",
                            hashlib.sha256(occurrence["source_xml"].encode()).hexdigest(),
                        ]
                    )
            tokens.append(block_tokens)
        question["fingerprint_version"] = "source-structure-v2"
        if has_content:
            fingerprint = hashlib.sha256(
                json.dumps(tokens, ensure_ascii=False).encode()
            ).hexdigest()
            question["content_fingerprint"] = fingerprint
            groups.setdefault(fingerprint, []).append(question["id"])
        else:
            question["content_fingerprint"] = None
    bank["exact_duplicate_groups"] = [ids for ids in groups.values() if len(ids) > 1]
    bank["fingerprint_version"] = "source-structure-v2"
    bank["deduplication_scope"] = (
        "normalized text, table/math structure and preview-image bytes; no semantic matching"
    )


def build(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists():
        raise ValueError("output directory exists; choose a new versioned output directory")
    if not source.is_dir():
        raise ValueError("source directory required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".question-bank-build-", dir=destination.parent))
    try:
        working = stage / "bank"
        bank = build_source_bank(source, working)
        add_exact_fingerprints(bank, working)
        bank = index_bank(bank, working)
        (working / "bank.json").write_text(
            json.dumps(bank, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        render_library(bank, working)
        pairs = {pair["id"]: pair for pair in bank["pairs"]}
        with (working / "catalog.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["题目ID", "试卷", "题号", "来源考点", "结构状态", "难度评分"])
            for question in bank["questions"]:
                writer.writerow(
                    [
                        question["id"],
                        pairs[question["pair_id"]]["title"],
                        question["number"],
                        "；".join(sorted({t["label"] for t in question.get("topics", [])})),
                        question["structure_status"],
                        question["difficulty_score"],
                    ]
                )
        summary = {
            "schema_version": bank["schema_version"],
            "batch_status": bank.get("batch_status", "succeeded"),
            "input_files": bank.get("counts", {}).get("input_files", len(bank["documents"])),
            "duplicate_files": bank.get("counts", {}).get("duplicate_files", 0),
            "failed_files": len(bank.get("failed_documents", [])),
            "documents": len(bank["documents"]),
            "pairs": len(bank["pairs"]),
            "questions": len(bank["questions"]),
            "questions_with_answers": sum(
                q.get("answer_available", False) for q in bank["questions"]
            ),
            "questions_with_topics": sum(bool(q.get("topics")) for q in bank["questions"]),
            "topic_count": len(
                {t["label"] for q in bank["questions"] for t in q.get("topics", [])}
            ),
            "processing_failed": sum(
                q["structure_status"] == "processing_failed" for q in bank["questions"]
            ),
            "association_confirmed": sum(
                q.get("association_status") == "confirmed" for q in bank["questions"]
            ),
            "association_unverified": sum(
                q.get("association_status") == "unverified" for q in bank["questions"]
            ),
            "association_confirmed_rate": (
                sum(q.get("association_status") == "confirmed" for q in bank["questions"])
                / len(bank["questions"])
                if bank["questions"]
                else None
            ),
            "association_comparison_method": "source_association_v3",
            "source_omml_count": bank.get("counts", {}).get("source_omml_count", 0),
            "rendered_omml_count": bank.get("counts", {}).get("rendered_omml_count", 0),
            "exact_duplicate_groups": len(bank["exact_duplicate_groups"]),
            "practice_ready": sum(q["practice_ready"] for q in bank["questions"]),
            "difficulty_scored": sum(q["difficulty_score"] is not None for q in bank["questions"]),
            "model_calls": 0,
            "issues": bank.get("issues", []),
            "failed_documents": bank.get("failed_documents", []),
        }
        (working / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (working / "使用说明.txt").write_text(
            "本地数学试题库\n\n"
            "打开 index.html 可检索题目、展开答案与解析，并下载对应原卷。\n"
            "移动或分享时请保留整个文件夹，不能只复制 index.html。\n"
            "catalog.csv 可用 Excel 打开；question-bank.sqlite 和 bank.json 供程序使用。\n\n"
            f"已配对 {summary['pairs']} 套试卷，共 {summary['questions']} 道大题；"
            f"{summary['questions_with_topics']} 道有来源考点标签。\n"
            f"构建状态：{summary['batch_status']}；题答已确认 {summary['association_confirmed']} 道，"
            f"待进一步自动核验 {summary['association_unverified']} 道。\n"
            "标签取自已有解析，未完成统一考点映射、前置知识及 AI 难度评分。\n"
            "公式以原文件预览图呈现，原 MathType 对象保留于 sources/ 的 DOCX 内。\n"
            "核验可比较完整原生公式字节；普通配图仍须原尺寸像素完全相同。\n"
            "来源对象相同不等于数学解答正确，也不表示所有题已可自动组卷。\n"
            "当前资料题库尚不能直接按难度自动组卷；结构统计不代表逐题数学语义验收。\n",
            encoding="utf-8",
        )
        if destination.exists():
            raise ValueError("output appeared during import; refusing to overwrite")
        os.rename(working, destination)
        return summary
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/private/question-bank-v3")
    args = parser.parse_args()
    try:
        summary = build(args.source, args.output)
    except (ValueError, OSError, DocxIngestError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Local browser: {args.output / 'index.html'}")
    return {"succeeded": 0, "partial": 3, "failed": 1}[summary["batch_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
