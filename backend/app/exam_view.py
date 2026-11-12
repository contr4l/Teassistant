"""Offline, source-linked HTML report rendering for private exam analysis."""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from app.contracts import Curriculum
from app.exam_contracts import (
    ExamAnalysis,
    ExamEvidence,
    ExamIntake,
    LearningSummary,
    PaperQuestion,
    StudentResponse,
)

_IMAGE_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
_OUTCOME_LABELS = {
    "correct": "正确",
    "incorrect": "错误",
    "partial": "部分得分",
    "unanswered": "未作答",
    "unknown": "未确定",
}
_ASSOCIATION_LABELS = {
    "linked": "已关联",
    "ambiguous": "待确认",
    "unmatched": "未关联",
}


def _normalize(value: Any, model):
    if isinstance(value, model):
        value = value.model_dump(mode="python")
    return model.model_validate(value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(payload.encode("utf-8"))


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _resolve_asset(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("exam page image path must be a safe relative path")
    if relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError("exam page image path cannot escape the asset root")
    posix = PurePosixPath(relative)
    if posix.is_absolute():
        raise ValueError("exam page image path must be relative")
    source = (root / Path(*posix.parts)).resolve(strict=True)
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("exam page image resolves outside the asset root") from exc
    if not source.is_file():
        raise ValueError("exam page image is not a regular file")
    return source


def _page_assets(intake: ExamIntake, asset_root: Path, stage: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    pages_dir = stage / "assets"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for page in intake.pages:
        source = _resolve_asset(asset_root, page.image_path)
        content = source.read_bytes()
        actual_hash = _sha256(content)
        if actual_hash != page.image_sha256:
            raise ValueError(f"source image hash changed for page {page.id}")
        try:
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                image_format = image.format
                width, height = image.size
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError(f"source page image is unreadable for page {page.id}") from exc
        if width != page.width or height != page.height:
            raise ValueError(f"source page dimensions changed for page {page.id}")
        extension = _IMAGE_EXTENSIONS.get(str(image_format).upper())
        if extension is None:
            raise ValueError(f"unsupported original image format for page {page.id}")
        name = f"page-{actual_hash[:20]}{extension}"
        destination = pages_dir / name
        if destination.exists() and _sha256(destination.read_bytes()) != actual_hash:
            raise ValueError("conflicting image asset path")
        if not destination.exists():
            shutil.copyfile(source, destination)
        output[page.id] = {
            "page_id": page.id,
            "source_page": page.source_page,
            "role": page.role,
            "path": f"assets/{name}",
            "sha256": actual_hash,
            "width": width,
            "height": height,
            "parent_page_id": page.parent_page_id,
            "parent_bbox": list(page.parent_bbox) if page.parent_bbox is not None else None,
        }
    return output


def _box_html(evidence, *, class_name: str, label: str) -> str:
    x0, y0, x1, y1 = evidence.bbox
    style = (
        f"left:{x0 * 100:.5f}%;top:{y0 * 100:.5f}%;"
        f"width:{(x1 - x0) * 100:.5f}%;height:{(y1 - y0) * 100:.5f}%"
    )
    return f'<span class="{class_name}" style="{style}" title="{_escape(label)}"></span>'


def _transform_crop_bbox(child, bbox):
    if child.parent_bbox is None:
        return bbox
    x0, y0, x1, y1 = bbox
    px0, py0, px1, py1 = child.parent_bbox
    return (
        px0 + x0 * (px1 - px0),
        py0 + y0 * (py1 - py0),
        px0 + x1 * (px1 - px0),
        py0 + y1 * (py1 - py0),
    )


def _page_sections(intake, assets, evidence_by_page) -> str:
    chunks = []
    for page in intake.pages:
        asset = assets[page.id]
        page_key = _escape(page.id)
        label = f"{page.role} · 第 {page.source_page} 页"
        overlays = []
        for evidence in evidence_by_page.get(page.id, []):
            overlays.append(_box_html(evidence, class_name="evidence-box", label=evidence.kind))
        for child in intake.pages:
            if child.parent_page_id == page.id:
                for evidence in evidence_by_page.get(child.id, []):
                    overlays.append(
                        _box_html(
                            evidence.model_copy(
                                update={"bbox": _transform_crop_bbox(child, evidence.bbox)}
                            ),
                            class_name="crop-box",
                            label=f"裁剪页 {child.id} 的来源区域",
                        )
                    )
        role = _escape(page.role)
        chunks.append(
            f'<figure class="source-page" id="source-{page_key}">'
            f"<figcaption>{_escape(label)} · {role}</figcaption>"
            f'<div class="page-image"><img src="{_escape(asset["path"])}" '
            f'alt="{_escape(label)}" width="{asset["width"]}" height="{asset["height"]}">'
            + "".join(overlays)
            + "</div></figure>"
        )
    return "\n".join(chunks)


def _evidence_links(evidence, page_map) -> str:
    rows = []
    for item in evidence:
        page = page_map.get(item.page_id)
        if page is None:
            continue
        x0, y0, x1, y1 = item.bbox
        rows.append(
            f'<a href="#source-{_escape(item.page_id)}">'
            f"{_escape(item.kind)} · {x0:.3f},{y0:.3f}–{x1:.3f},{y1:.3f}"
            f"{(' · ' + _escape(item.excerpt)) if item.excerpt else ''}</a>"
        )
    return "<br>".join(rows) or "无有效定位证据"


def _response_texts(question, responses_by_id, paper=None) -> list[str]:
    output = []
    matched_responses = []
    for response_id in question.association.response_ids:
        matches = responses_by_id.get(response_id, [])
        if len(matches) != 1:
            output.append(f"响应 {response_id}：ID 重复或缺失，内容不可确定")
            continue
        response = matches[0]
        matched_responses.append(response)
        legibility = {
            "readable": "清晰可辨",
            "partial": "部分可辨，请以原图为准",
            "illegible": "无法辨认，请以原图为准",
            "blank": "识别为空白，不能据此推断计零分",
        }[response.legibility]
        label = "AI转录" if question.association.status == "linked" else "候选AI转录（关联未确认）"
        output.append(
            f"{label}（{legibility}） {response.part_path or '整题'}："
            f"{response.transcript or '（无可辨认转录）'}"
        )
        if response.alternatives:
            output.append("其他AI转录候选：" + "；".join(response.alternatives))
    if question.association.status == "linked" and paper is not None and paper.part_paths:
        if any(not response.part_path for response in matched_responses):
            output.append("小问覆盖：有整题作答记录，但未按小问拆分；不能据此判断各小问是否缺答。")
        else:
            present_paths = {response.part_path for response in matched_responses}
            leaf_paths = [
                path
                for path in paper.part_paths
                if not any(other.startswith(f"{path}/") for other in paper.part_paths)
            ]
            missing_paths = [path for path in leaf_paths if path not in present_paths]
            if missing_paths:
                output.append(
                    "小问覆盖：识别结果未关联到此小问，不能据此认定未作答或计零分："
                    + "、".join(missing_paths)
                )
    return output


def _question_html(question, *, paper, responses_by_id, references, points, page_map) -> str:
    number = question.number
    paper_text = paper.text if paper else "未能唯一识别原卷题干"
    response_text = "<br>".join(
        _escape(item) for item in _response_texts(question, responses_by_id, paper)
    )
    if not response_text:
        response_text = "未找到可唯一关联的作答"
    reference = [item for item in references if item.number == number]
    reference_html = (
        "<br>".join(
            f"{_escape(item.part_path or '整题')}：{_escape(item.text)} "
            f"({_evidence_links(item.sources, page_map)})"
            for item in reference
        )
        or "未提供已验证的参考答案；不会用总分推断本题对错"
    )
    point_names = [points[point_id] for point_id in question.point_ids if point_id in points]
    score = (
        f"得分 {question.awarded:g} / 满分 {question.maximum:g}"
        if question.awarded is not None and question.maximum is not None
        else f"已读到失分 {question.deducted:g} / 满分 {question.maximum:g}"
        if question.deducted is not None and question.maximum is not None
        else f"满分 {question.maximum:g}，得分未能可靠确认"
        if question.maximum is not None
        else "得分/满分未能可靠确认"
    )
    grading_html = (
        "<br>".join(
            f"{_escape(item.part_path or '整题')} · {_escape(item.literal)} "
            f"({_evidence_links(item.sources, page_map)})"
            for item in question.grading_evidence
        )
        or "未找到可绑定到本题的直接批改分值"
    )
    parts_html = ""
    if question.part_diagnoses:
        rows = []
        for proposal in question.part_diagnoses:
            status = {
                "supported": "作答证据支持",
                "hypothesis": "模型假设，待核对",
                "unknown": "未确定",
            }[proposal.reason_status]
            labels = (
                ", ".join(points[point_id] for point_id in proposal.point_ids if point_id in points)
                or "未标记"
            )
            rows.append(
                "<li><b>"
                + _escape(proposal.part_path or "整题")
                + f" · {_escape(_OUTCOME_LABELS[proposal.outcome])} · {_escape(status)}:</b> "
                + f"考点：{_escape(labels)}；原因：{_escape(proposal.reason or '未提供')}；依据："
                + _evidence_links(proposal.evidence, page_map)
                + "</li>"
            )
        parts_html = "<h4>分问分析（不向小问分摊整题分值）</h4><ul>" + "".join(rows) + "</ul>"
    warnings_html = (
        "<ul>" + "".join(f"<li>{_escape(item)}</li>" for item in question.warnings) + "</ul>"
        if question.warnings
        else ""
    )
    return (
        f'<article class="question" id="question-{number}">'
        f"<h3>第 {number} 题 · {_escape(_OUTCOME_LABELS[question.outcome])} · "
        f"{_escape(_ASSOCIATION_LABELS[question.association.status])}</h3>"
        f'<p class="transcription"><b>原卷识别：</b>{_escape(paper_text)}</p>'
        f'<p class="transcription"><b>学生作答：</b>{response_text}</p>'
        f"<p><b>参考答案：</b>{reference_html}</p>"
        f"<p><b>分值记录：</b>{_escape(score)}</p>"
        f"<p><b>批改证据：</b>{grading_html}</p>"
        f"<p><b>考点：</b>{_escape(', '.join(point_names) if point_names else '未确认')}</p>"
        f"<p><b>分析说明：</b>{_escape(question.reason or '暂无可支持的原因结论')} "
        f"（{_escape(question.reason_status)}）</p>"
        f"<p><b>建议动作：</b>{_escape(question.suggested_action or '暂无')}</p>"
        f"<p><b>来源定位：</b>{_evidence_links(question.evidence, page_map)}</p>"
        + parts_html
        + warnings_html
        + "</article>"
    )


def _summary_html(summary: LearningSummary | None, curriculum_points) -> str:
    if summary is None:
        return ""
    rows = []
    for item in summary.points:
        name = curriculum_points.get(item.point_id, item.point_id)
        rows.append(
            f"<tr><td>{_escape(name)}</td><td>{item.error_question_count}/{item.observed_question_count}</td>"
            f"<td>{_escape(', '.join(item.exam_ids))}</td></tr>"
        )
    warnings = (
        "<ul>" + "".join(f"<li>{_escape(warning)}</li>" for warning in summary.warnings) + "</ul>"
        if summary.warnings
        else ""
    )
    return (
        "<section><h2>近期考试学习信号</h2>"
        f"<p>实际纳入 {summary.actual_exam_count} 场：{_escape(', '.join(summary.selected_exam_ids))}。"
        "错误次数是练习信号，不表示已掌握或未掌握。</p>"
        "<table><thead><tr><th>考点</th><th>错误/已观察</th><th>考试</th></tr></thead><tbody>"
        + "".join(rows)
        + f"</tbody></table><p>未确定题目数：{summary.unresolved_question_count}</p>{warnings}</section>"
    )


def render_exam_report(
    analysis: ExamAnalysis,
    *,
    intake: ExamIntake,
    asset_root: str | Path,
    output_dir: str | Path,
    summary: LearningSummary | None = None,
    curriculum: Curriculum | None = None,
) -> dict[str, Any]:
    analysis = _normalize(analysis, ExamAnalysis)
    intake = _normalize(intake, ExamIntake)
    if analysis.exam_id != intake.exam_id or analysis.student_id != intake.student_id:
        raise ValueError("report intake does not match the exam analysis")
    if analysis.expected_question_numbers != intake.expected_question_numbers:
        raise ValueError("report question coverage differs from the fixed intake manifest")
    if analysis.is_synthetic_student != intake.is_synthetic_student:
        raise ValueError("report synthetic-student flag differs from intake")
    if curriculum is not None:
        curriculum = _normalize(curriculum, Curriculum)
        if curriculum.version != analysis.curriculum_version:
            raise ValueError("report curriculum version differs from the analysis")
        if _canonical_sha(curriculum) != analysis.curriculum_sha256:
            raise ValueError("report curriculum content differs from the analysis")
    if summary is not None:
        summary = _normalize(summary, LearningSummary)
        if summary.student_id != analysis.student_id:
            raise ValueError("learning summary belongs to another student")
        if summary.curriculum_version != analysis.curriculum_version:
            raise ValueError("learning summary curriculum differs")
        if curriculum is not None and summary.curriculum_version != curriculum.version:
            raise ValueError("learning summary curriculum differs from the supplied curriculum")
    root = Path(asset_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("asset_root must be a directory")
    final = Path(output_dir).resolve()
    if final == root or final.is_relative_to(root):
        raise ValueError("report output cannot be inside the source asset root")
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        raise FileExistsError("report output directory must be fresh")
    page_map = {page.id: page for page in intake.pages}
    if len(page_map) != len(intake.pages):
        raise ValueError("intake page identifiers are not unique")
    referenced_evidence = [e for q in analysis.questions for e in q.evidence]
    referenced_evidence.extend(
        e for q in analysis.questions for row in q.grading_evidence for e in row.sources
    )
    referenced_evidence.extend(
        e for q in analysis.questions for part in q.part_diagnoses for e in part.evidence
    )
    referenced_evidence.extend(e for answer in analysis.reference_answers for e in answer.sources)
    referenced_evidence.extend(e for row in analysis.observed_totals for e in row.sources)
    referenced_evidence.extend(
        e
        for question in analysis.paper_questions
        for e in question.sources + question.score_sources
    )
    referenced_evidence.extend(
        e
        for response in analysis.student_responses
        for e in response.sources + response.paper_sources
    )
    if any(e.page_id not in page_map for e in referenced_evidence):
        raise ValueError("analysis evidence refers to a missing intake page")
    source_question_records: dict[int, list[PaperQuestion]] = defaultdict(list)
    for question in analysis.paper_questions:
        source_question_records[question.number].append(question)
    responses_by_id: dict[str, list[StudentResponse]] = defaultdict(list)
    for response in analysis.student_responses:
        responses_by_id[response.id].append(response)
    point_labels = (
        {point.id: point.name for point in curriculum.points}
        if curriculum is not None
        else {point_id: point_id for q in analysis.questions for point_id in q.point_ids}
    )
    if summary is not None:
        if curriculum is None:
            point_labels.update({item.point_id: item.point_id for item in summary.points})
    stage = Path(tempfile.mkdtemp(prefix=f".{final.name}.stage-", dir=final.parent))
    try:
        assets = _page_assets(intake, root, stage)
        evidence_by_page: dict[str, list[ExamEvidence]] = defaultdict(list)
        for question in analysis.questions:
            for evidence in question.evidence:
                evidence_by_page[evidence.page_id].append(evidence)
            for observation in question.grading_evidence:
                for evidence in observation.sources:
                    evidence_by_page[evidence.page_id].append(evidence)
            for proposal in question.part_diagnoses:
                for evidence in proposal.evidence:
                    evidence_by_page[evidence.page_id].append(evidence)
        for answer in analysis.reference_answers:
            for evidence in answer.sources:
                evidence_by_page[evidence.page_id].append(evidence)
        for observation in analysis.observed_totals:
            for evidence in observation.sources:
                evidence_by_page[evidence.page_id].append(evidence)

        question_html = []
        for question in analysis.questions:
            source_candidates = source_question_records.get(question.number, [])
            paper = source_candidates[0] if len(source_candidates) == 1 else None
            question_html.append(
                _question_html(
                    question,
                    paper=paper,
                    responses_by_id=responses_by_id,
                    references=analysis.reference_answers,
                    points=point_labels,
                    page_map=page_map,
                )
            )
        totals = (
            "<ul>"
            + "".join(
                f"<li>{_escape(item.kind)}：{_escape(item.literal)} = {_escape(item.value)} "
                f"({_evidence_links(item.sources, page_map)})</li>"
                for item in analysis.observed_totals
            )
            + "</ul>"
            if analysis.observed_totals
            else "<p>未识别到有来源定位的整卷总分记录</p>"
        )
        report_warnings = (
            "<ul>" + "".join(f"<li>{_escape(item)}</li>" for item in analysis.warnings) + "</ul>"
            if analysis.warnings
            else "<p>无全局警告</p>"
        )
        synthetic = (
            '<p class="synthetic">开发验证样例（合成学生材料）</p>'
            if analysis.is_synthetic_student
            else ""
        )
        html_text = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>考试分析</title><style>"
            "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#20242a}"
            "h1,h2,h3{line-height:1.35}article,section{border:1px solid #ccd2d9;border-radius:8px;padding:1rem;margin:1rem 0}"
            ".synthetic{font-weight:700;color:#8b3d00;background:#fff2df;padding:.7rem}"
            ".page-image{position:relative;display:inline-block;max-width:100%;line-height:0}"
            ".page-image img{display:block;max-width:min(100%,850px);height:auto}"
            ".transcription{white-space:pre-wrap}"
            ".evidence-box,.crop-box{position:absolute;border:2px solid #d2232a;box-sizing:border-box;pointer-events:none}"
            ".crop-box{border-color:#2466d1;border-style:dashed}figure{margin:1rem 0;padding:1rem;background:#f7f8fa}"
            "a{color:#1457a6}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbc2ca;padding:.5rem;text-align:left}"
            ".source-page{break-inside:avoid} .question h3{border-bottom:1px solid #ddd;padding-bottom:.4rem}"
            "</style></head><body>"
            f"<h1>考试分析 · {_escape(analysis.exam_id)}</h1>{synthetic}"
            f"<p>学生：{_escape(analysis.student_id)} · 考试日期：{_escape(analysis.exam_date or '未提供')} · "
            f"目录版本：{_escape(analysis.curriculum_version)}</p>"
            "<p>这是基于当前材料的分析记录；未确定内容保留未确定状态，原因假设不等于掌握结论。</p>"
            f"<section><h2>整卷分值记录</h2><p>题目分值汇总："
            f"{_escape(analysis.total_awarded if analysis.total_awarded is not None else '未完整确定')} / "
            f"{_escape(analysis.total_maximum if analysis.total_maximum is not None else '未完整确定')}</p>"
            f"<h3>材料中识别到的整卷总分（单独保留）</h3>{totals}</section>"
            f"{_summary_html(summary, point_labels)}"
            "<section><h2>逐题分析</h2>"
            + "".join(question_html)
            + "</section><section><h2>原始页面与定位框</h2>"
            + _page_sections(intake, assets, evidence_by_page)
            + f"</section><section><h2>全局提示</h2>{report_warnings}</section>"
            "</body></html>"
        )
        manifest = {
            "schema_version": "exam-report-v1",
            "exam_id": analysis.exam_id,
            "student_id": analysis.student_id,
            "revision": analysis.revision,
            "analysis": analysis.model_dump(mode="json"),
            "learning_summary": summary.model_dump(mode="json") if summary else None,
            "pages": list(assets.values()),
        }
        html_path = stage / "report.html"
        json_path = stage / "report.json"
        html_path.write_text(html_text, encoding="utf-8")
        json_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if not html_path.is_file() or not json_path.is_file():
            raise ValueError("report files were not written")
        os.replace(stage, final)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "html_path": str(final / "report.html"),
        "json_path": str(final / "report.json"),
        "asset_paths": [str(final / item["path"]) for item in assets.values()],
    }
