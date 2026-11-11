"""Build a safe, portable HTML/CSV review of semantic assessment annotations."""

from __future__ import annotations

import csv
import hashlib
import html
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image

from app.contracts import Curriculum, difficulty_level

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")
_STATUS_NAMES = {
    "assessed": "已评级",
    "failed": "失败",
    "not_attempted": "未尝试",
    "running": "进行中",
}
_LEVEL_NAMES = {"basic": "基础", "intermediate": "提高", "advanced": "挑战"}
_MIGRATION_NAMES = {
    "carried_forward_not_semantically_reassessed": "原目录初评，已通过新目录兼容校验",
    "model_reassessment": "使用新目录重新评级",
}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _esc(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _safe_component(value: str) -> str:
    result = _SAFE_COMPONENT.sub("_", value).strip("._")
    return result[:100] or "question"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_image_source(root: Path, image: dict) -> tuple[Path, int, int, str]:
    raw_path = image.get("path")
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise ValueError("input image path must be a relative path")
    relative = Path(raw_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("input image path escapes input root")
    path = (root / relative).resolve(strict=True)
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError("input image is missing or escapes input root")
    expected_hash = image.get("sha256")
    if not isinstance(expected_hash, str) or _sha256(path) != expected_hash:
        raise ValueError("input image SHA-256 does not match manifest")
    try:
        with Image.open(path) as raster:
            if raster.format != "PNG":
                raise ValueError("semantic preview image must be PNG")
            width, height = raster.size
            raster.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("semantic preview image is invalid") from exc
    if (
        type(image.get("width")) is not int
        or type(image.get("height")) is not int
        or image["width"] != width
        or image["height"] != height
    ):
        raise ValueError("input image dimensions do not match manifest")
    return path, width, height, expected_hash


def _assessment(row: dict) -> dict:
    value = row.get("assessment")
    return value if isinstance(value, dict) else {}


def _labels(ids: object, names: dict[str, str]) -> list[tuple[str, str]]:
    if not isinstance(ids, list):
        return []
    return [
        (point_id, names.get(point_id, "目录中无此标签"))
        for point_id in ids
        if isinstance(point_id, str)
    ]


def _program_added_prerequisites(row: dict, assessment: dict) -> list[str]:
    required = row.get("required_point_ids")
    targets = assessment.get("point_ids")
    explicit = assessment.get("prerequisite_ids")
    if not isinstance(required, list):
        return []
    excluded = {
        value
        for values in (targets, explicit)
        if isinstance(values, list)
        for value in values
        if isinstance(value, str)
    }
    return [value for value in required if isinstance(value, str) and value not in excluded]


def _score_info(assessment: dict) -> tuple[int | None, str]:
    score = assessment.get("difficulty_score")
    if type(score) is not int or not 0 <= score <= 100:
        return None, ""
    return score, _LEVEL_NAMES[difficulty_level(score)]


def _csv_cell(value: object) -> str:
    text = _text(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _render_report_html(rows: dict, cards: list[str], curriculum: Curriculum) -> str:
    counts = Counter(_text(row.get("status")) for row in rows.values())
    unverified = sum(row.get("association_status") != "confirmed" for row in rows.values())
    distributions = Counter()
    for row in rows.values():
        assessment = _assessment(row)
        score, level = _score_info(assessment)
        if row.get("status") == "assessed" and score is not None:
            distributions[level] += 1
    points = sorted(
        {
            point_id
            for row in rows.values()
            if row.get("status") == "assessed"
            for point_id, _ in _labels(_assessment(row).get("point_ids"), {})
        }
    )
    point_names = {point.id: point.name for point in curriculum.points if point.id in points}
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    cards_html = "".join(cards)
    point_options = "".join(
        f'<option value="{_esc(point_id)}">{_esc(point_names.get(point_id, point_id))}</option>'
        for point_id in sorted(points)
    )
    status_summary = " · ".join(
        f"{label} {counts.get(code, 0)}"
        for code, label in (
            ("assessed", "已评级"),
            ("failed", "失败"),
            ("not_attempted", "未尝试"),
            ("running", "进行中"),
        )
    )
    distribution_summary = " · ".join(
        f"{level} {distributions.get(level, 0)}" for level in ("基础", "提高", "挑战")
    )
    origins = Counter(
        row.get("migration_method") for row in rows.values() if row.get("status") == "assessed"
    )
    origin_summary = " · ".join(
        f"{label} {origins[method]} 题"
        for method, label in _MIGRATION_NAMES.items()
        if origins[method]
    )
    origin_html = f'<p class="muted">{_esc(origin_summary)}</p>' if origin_summary else ""
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>AI考点与难度初评</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f2f5f8;color:#192533;font:16px/1.65 system-ui,"PingFang SC","Microsoft YaHei",sans-serif}}
header,main{{max-width:1180px;margin:auto;padding:24px}}header{{padding-bottom:10px}}h1{{font-size:28px;margin:0 0 8px}}.notice{{background:#fff4d9;border:1px solid #ead59a;padding:12px 16px;border-radius:8px;color:#594500}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}.stat{{background:white;border:1px solid #dce3e9;border-radius:9px;padding:12px 16px;min-width:165px}}.stat b{{display:block;font-size:21px}}
.filters{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}input,select{{font:inherit;border:1px solid #bcc8d3;border-radius:7px;background:white;padding:10px}}input{{flex:1;min-width:230px}}
article{{background:white;border:1px solid #dce3e9;border-radius:10px;padding:20px;margin:14px 0;overflow-wrap:anywhere}}h2{{font-size:19px;margin:0 0 8px}}.status{{display:inline-block;border-radius:20px;background:#eaf0f5;padding:2px 10px;font-size:13px}}.badges{{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}}.badge{{background:#eef3fa;color:#284d73;padding:3px 9px;border-radius:5px;font-size:14px}}
.muted{{color:#596879}}.warning{{color:#8c4e00;background:#fff5e8;padding:8px 12px;border-radius:6px}}.label-list{{margin:8px 0;padding-left:22px}}details{{border-top:1px solid #e2e7eb;margin-top:14px;padding-top:10px}}summary{{cursor:pointer;font-weight:600}}.previews img{{display:block;max-width:100%;height:auto;margin:12px auto;border:1px solid #d4dbe1}}.evidence{{border-left:3px solid #aab8c6;padding-left:10px;margin:7px 0}}.closure{{background:#f1f4f7;padding:10px;border-radius:6px}}.empty{{padding:20px;color:#596879}}
</style></head><body><header><h1>AI考点与难度初评</h1>
<div class="notice">工程目录草案；难度为模型初评，尚未教师校准，未发布为组卷题库或可组卷题目。题答关联未确认时只允许依据题干评级，绝不使用答案与解析；未成功项仍保留在实际分母中。</div>
<section class="stats" aria-label="批次计数">
<div class="stat">实际分母<b>{len(rows)}</b></div><div class="stat">批次状态<b>{_esc(status_summary)}</b></div>
<div class="stat">未确认题答关联<b>{unverified}</b></div><div class="stat">难度分布（已评级）<b>{_esc(distribution_summary)}</b></div>
<div class="stat">目录版本<b>{_esc(curriculum.version)}</b></div></section>{origin_html}
<div class="filters"><input id="search" type="search" placeholder="搜索题号、考点、理由或警告" aria-label="搜索">
<select id="point"><option value="">全部考点</option>{point_options}</select>
<select id="difficulty"><option value="">全部难度</option><option value="basic">基础</option><option value="intermediate">提高</option><option value="advanced">挑战</option></select></div>
<p id="visible-count" class="muted"></p><p><a href="annotations.csv" download>下载批注 CSV</a></p></header><main>{cards_html}<p id="empty" class="empty" hidden>没有匹配的题目。</p></main>
<script nonce="{nonce}">
const cards=[...document.querySelectorAll('article')],search=document.getElementById('search'),point=document.getElementById('point'),difficulty=document.getElementById('difficulty'),count=document.getElementById('visible-count'),empty=document.getElementById('empty');
function update(){{const term=search.value.toLocaleLowerCase();let n=0;for(const card of cards){{const ok=card.dataset.search.includes(term)&&(!point.value||card.dataset.points.split(' ').includes(point.value))&&(!difficulty.value||card.dataset.difficulty===difficulty.value);card.hidden=!ok;if(ok)n++}}count.textContent=`显示 ${{n}} / ${{cards.length}} 道题`;empty.hidden=n!==0}}
search.addEventListener('input',update);point.addEventListener('change',update);difficulty.addEventListener('change',update);update();
</script></body></html>'''


def render_semantic_report(
    output_dir: Path,
    rows: dict,
    curriculum: Curriculum,
    inputs: dict,
    input_dir: Path,
) -> Path:
    """Write a standalone review report without changing semantic run evidence."""
    if (
        not isinstance(rows, dict)
        or not rows
        or not isinstance(inputs, dict)
        or set(rows) != set(inputs)
    ):
        raise ValueError("rows and inputs must contain the same complete question cohort")
    input_root = Path(input_dir).resolve(strict=True)
    if not input_root.is_dir():
        raise ValueError("input_dir must be a directory")
    target = Path(output_dir).expanduser().absolute()
    target_resolved = target.resolve(strict=False)
    if (
        input_root == target_resolved
        or input_root in target_resolved.parents
        or target_resolved in input_root.parents
    ):
        raise ValueError("report output must be separate from immutable input directory")
    existing = target.exists()
    if existing and not target.is_dir():
        raise ValueError("output_dir must be a directory")
    if target.is_symlink():
        raise ValueError("report output directory must not be a symlink")

    names = {point.id: point.name for point in curriculum.points}
    prepared = []
    for index, (qid, row) in enumerate(rows.items(), 1):
        if (
            not isinstance(qid, str)
            or not isinstance(row, dict)
            or not isinstance(inputs[qid], dict)
        ):
            raise ValueError("invalid semantic report row")
        entry = inputs[qid]
        association = row.get("association_status")
        if entry.get("association_status") != association:
            raise ValueError("row association status differs from semantic input")
        if entry.get("reliable_solution_used") is not (association == "confirmed"):
            raise ValueError("semantic input solution provenance is inconsistent")
        images = entry.get("images")
        if not isinstance(images, list) or not images:
            raise ValueError(f"question {qid} has no complete input images")
        prepared_images = []
        qdir = f"q{index:03d}-{_safe_component(qid)}"
        for image_index, image in enumerate(images, 1):
            if not isinstance(image, dict):
                raise ValueError("invalid image manifest entry")
            source, width, height, digest = _safe_image_source(input_root, image)
            filename = f"tile-{image_index:03d}.png"
            relative = Path("preview-images") / qdir / filename
            destination = target / relative
            if not destination.resolve(strict=False).is_relative_to(target_resolved):
                raise ValueError("preview output path escapes report directory")
            if destination.exists():
                if destination.is_symlink():
                    raise ValueError("existing preview image must not be a symlink")
                resolved_destination = destination.resolve(strict=True)
                if not resolved_destination.is_relative_to(target_resolved):
                    raise ValueError("existing preview image escapes report output")
                if not resolved_destination.is_file() or _sha256(resolved_destination) != digest:
                    raise ValueError(
                        "existing preview image differs from source; refusing overwrite"
                    )
            prepared_images.append(
                {
                    "path": relative.as_posix(),
                    "width": width,
                    "height": height,
                    "sha256": digest,
                    "source": source,
                }
            )
        prepared.append((qid, row, prepared_images))

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.semantic-stage-", dir=target.parent))
    try:
        html_cards = []
        csv_path = stage / "annotations.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "题目ID",
                    "状态",
                    "题答关联",
                    "目标考点ID",
                    "目标考点",
                    "模型前置ID",
                    "模型前置考点",
                    "程序补足前置ID",
                    "程序补足前置考点",
                    "难度分",
                    "难度等级",
                    "理由",
                    "逐标签依据",
                    "模型警告",
                    "错误代码",
                    "评级来源",
                    "模型初评目录",
                    "当前校验目录",
                ]
            )
            for qid, row, images in prepared:
                status = _text(row.get("status"))
                status_label = _STATUS_NAMES.get(status, f"未知状态（{status}）")
                association = _text(row.get("association_status"))
                if association == "confirmed":
                    association_label = "已确认"
                elif status == "assessed":
                    association_label = "关联未确认：仅题干评级，答案/解析未用"
                else:
                    association_label = "关联未确认：答案/解析未用，本题未完成评级"
                assessment = _assessment(row) if status == "assessed" else {}
                targets = _labels(assessment.get("point_ids"), names)
                model_prereqs = _labels(assessment.get("prerequisite_ids"), names)
                program_prereqs = _labels(_program_added_prerequisites(row, assessment), names)
                score, level = _score_info(assessment)
                reasoning = _text(assessment.get("reasoning"))
                evidence = assessment.get("point_evidence", [])
                evidence_items = []
                if isinstance(evidence, list):
                    for item in evidence:
                        if not isinstance(item, dict):
                            continue
                        pid = item.get("point_id")
                        if not isinstance(pid, str):
                            continue
                        role = {
                            "target": "目标考点",
                            "prerequisite": "模型显式前置",
                        }.get(item.get("role"), "未知标签角色")
                        evidence_items.append(
                            (role, names.get(pid, "目录中无此标签"), pid, _text(item.get("reason")))
                        )
                warnings = assessment.get("warnings", [])
                warnings = (
                    [_text(warning) for warning in warnings if isinstance(warning, str)]
                    if isinstance(warnings, list)
                    else []
                )
                error = _text(row.get("error_code"))
                target_text = "；".join(f"{name} [{pid}]" for pid, name in targets)
                model_prereq_text = "；".join(f"{name} [{pid}]" for pid, name in model_prereqs)
                program_prereq_text = "；".join(f"{name} [{pid}]" for pid, name in program_prereqs)
                evidence_csv = "；".join(
                    f"{role} {name} [{pid}]：{reason}" for role, name, pid, reason in evidence_items
                )
                warning_csv = "；".join(warnings)
                writer.writerow(
                    [
                        *(
                            _csv_cell(value)
                            for value in (
                                qid,
                                status_label,
                                association_label,
                                " ".join(pid for pid, _ in targets),
                                target_text,
                                " ".join(pid for pid, _ in model_prereqs),
                                model_prereq_text,
                                " ".join(pid for pid, _ in program_prereqs),
                                program_prereq_text,
                            )
                        ),
                        score if score is not None else "",
                        level,
                        _csv_cell(reasoning),
                        _csv_cell(evidence_csv),
                        _csv_cell(warning_csv),
                        _csv_cell(error),
                        _csv_cell(_MIGRATION_NAMES.get(row.get("migration_method"), "")),
                        _csv_cell(row.get("assessment_catalog_version")),
                        _csv_cell(row.get("validated_catalog_version")),
                    ]
                )

                target_badges = (
                    "".join(
                        f'<span class="badge">{_esc(name)} <code>{_esc(pid)}</code></span>'
                        for pid, name in targets
                    )
                    or '<span class="muted">暂无有效目标考点</span>'
                )
                closure_html = (
                    "；".join(f"{_esc(name)} [{_esc(pid)}]" for pid, name in program_prereqs)
                    or "无"
                )
                evidence_html = (
                    "".join(
                        f'<p class="evidence"><b>{_esc(role)}：{_esc(name)} [{_esc(pid)}]</b><br>{_esc(reason)}</p>'
                        for role, name, pid, reason in evidence_items
                    )
                    or '<p class="muted">没有逐标签模型依据。</p>'
                )
                warnings_html = (
                    "".join(f"<li>{_esc(warning)}</li>" for warning in warnings) or "<li>无</li>"
                )
                previews = "".join(
                    f'<img src="{_esc(image["path"])}" width="{image["width"]}" height="{image["height"]}" alt="题目完整输入图 {image_index}" loading="lazy">'
                    for image_index, image in enumerate(images, 1)
                )
                search_text = " ".join(
                    [qid, target_text, model_prereq_text, program_prereq_text, reasoning, *warnings]
                ).lower()
                points_attr = " ".join(pid for pid, _ in targets)
                score_html = f"{score} 分 · {_esc(level)}" if score is not None else "未评级"
                origin = _MIGRATION_NAMES.get(row.get("migration_method"), "")
                origin_html = f'<p class="muted">评级来源：{_esc(origin)}</p>' if origin else ""
                html_cards.append(
                    f'<article data-search="{_esc(search_text)}" data-points="{_esc(points_attr)}" data-difficulty="{_esc(difficulty_level(score) if score is not None else "")}">'
                    f'<h2>{_esc(qid)}</h2><p><span class="status">{_esc(status_label)}</span> · {_esc(association_label)} · {score_html}</p>'
                    f"{origin_html}"
                    f'<div class="badges">{target_badges}</div><p>{_esc(reasoning) or "暂无模型理由。"}</p>'
                    f'<details><summary>查看完整输入图、逐标签依据与警告</summary><div class="previews">{previews}</div>'
                    f'<h3>模型逐标签依据</h3>{evidence_html}<h3>程序补足的前置闭包（不是模型显式证据）</h3><p class="closure">{closure_html}</p>'
                    f'<h3>模型警告</h3><ul>{warnings_html}</ul><p class="muted">错误代码：{_esc(error) or "无"}</p></details></article>'
                )
        # Stage every new preview and both report files before publishing anything.
        for _, _, images in prepared:
            for image in images:
                relative = Path(image["path"])
                destination = target / relative
                if existing and destination.exists():
                    continue
                staged = stage / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image["source"], staged)
                if _sha256(staged) != image["sha256"]:
                    raise ValueError("staged preview image failed SHA-256 verification")
        (stage / "index.html").write_text(
            _render_report_html(rows, html_cards, curriculum), encoding="utf-8"
        )
        if not existing:
            stage.rename(target)
            return target / "index.html"
        for _, _, images in prepared:
            for image in images:
                relative = Path(image["path"])
                destination = target / relative
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / relative, destination)
        os.replace(stage / "index.html", target / "index.html")
        os.replace(csv_path, target / "annotations.csv")
        return target / "index.html"
    finally:
        shutil.rmtree(stage, ignore_errors=True)
