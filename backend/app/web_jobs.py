"""Persistent business jobs. HTTP handlers only enqueue immutable inputs."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from app.bank_practice import build_bank_practice, load_practice_bank
from app.contracts import Curriculum, PracticeRequest, StudentProgress
from app.curriculum import CurriculumIndex
from app.exam_contracts import ExamAnalysis, ExamCorrection, ExamModelResponse
from app.prompt_templates import build_instructions, prompt_digest
from app.semantic_contracts import DIFFICULTY_VERSION
from app.web_settings import ROOT


class JobInputError(ValueError):
    """A deliberately public task validation message without raw source content."""


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_file(settings, relative):
    """A persisted private locator is still checked on every read."""
    if not isinstance(relative, str) or "\\" in relative:
        raise JobInputError("invalid private file locator")
    pure = Path(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise JobInputError("private file locator escapes storage")
    root = settings.data_dir.resolve()
    file = (root / pure).resolve(strict=True)
    if not file.is_relative_to(root) or not file.is_file():
        raise JobInputError("private file is unavailable")
    return file


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _relative(settings, path):
    return str(Path(path).resolve().relative_to(settings.data_dir.resolve()))


def _curriculum(settings):
    return Curriculum.model_validate_json(settings.curriculum_path.read_bytes())


def _bank(settings, store=None):
    if store is not None:
        from app.admin_bank import load_bank

        return load_bank(store, settings)
    if settings.bank_dir is None or settings.semantic_dir is None:
        raise JobInputError("尚未配置可组卷题库，请由管理员设置题库与语义目录")
    bank = load_practice_bank(settings.bank_dir, settings.semantic_dir)
    if bank.curriculum_version != _curriculum(settings).version:
        raise JobInputError("题库与学生考点目录版本不同")
    return bank


def _student(store, owner, identifier):
    row = store.get_record("student", identifier, owner)
    if row is None:
        raise JobInputError("学生不存在")
    return row


def _progress(row, curriculum):
    data = row["data"]
    return StudentProgress(
        student_id=row["id"],
        revision=str(data.get("progress_revision", row["revision"])),
        curriculum_version=curriculum.version,
        learned_point_ids=data.get("learned_point_ids", []),
    )


def get_inventory(store, settings, owner_id, student_id):
    bank = _bank(settings, store)
    curriculum = _curriculum(settings)
    index = CurriculumIndex(curriculum)
    progress = _progress(_student(store, owner_id, student_id), curriculum)
    learned = set(progress.learned_point_ids)
    eligible = [
        q
        for q in bank.questions
        if q.status == "eligible" and index.required(q.point_ids + q.prerequisite_ids) <= learned
    ]
    return {
        "curriculum_version": curriculum.version,
        "progress_revision": progress.revision,
        "eligible_within_progress": len(eligible),
        "points": [
            {
                "point_id": point.id,
                "name": point.name,
                "candidate_count": sum(point.id in q.point_ids for q in eligible),
                "by_difficulty": {
                    level: sum(point.id in q.point_ids and q.difficulty == level for q in eligible)
                    for level in ("basic", "intermediate", "advanced")
                },
            }
            for point in curriculum.points
        ],
    }


def build_job_input(store, settings, kind, resource_record):
    """Capture materials, progress, analysis and editable prompt versions at submit."""
    if kind == "bank_import":
        from app.admin_bank import state

        return {
            **copy.deepcopy(resource_record["data"]),
            "resource_revision": resource_record["revision"],
            "bank_revision": state(store)["revision"],
            "curriculum_sha256": _sha(settings.curriculum_path),
        }
    owner = resource_record["owner_id"]
    data = resource_record["data"]
    student = _student(store, owner, data["student_id"])
    curriculum = _curriculum(settings)
    snapshot = store.snapshot_prompts()
    for key in ("layout", "paper", "reference", "response", "diagnosis", "report"):
        build_instructions(key, snapshot)
    result = {
        "student_id": student["id"],
        "student_record_revision": student["revision"],
        "progress_revision": _progress(student, curriculum).revision,
        "learned_point_ids": student["data"].get("learned_point_ids", []),
        "curriculum_sha256": _sha(settings.curriculum_path),
        "prompt_snapshot": snapshot,
        "prompt_sha256": prompt_digest(snapshot),
        "resource_revision": resource_record["revision"],
        "resource_data": copy.deepcopy(data),
    }
    if kind in {"analysis", "correction"}:
        assets = store.list_records("asset", owner, resource_record["id"])
        assets.sort(key=lambda r: (r["data"]["role"], r["data"]["ordinal"], r["id"]))
        if not any(r["data"]["role"] == "paper" for r in assets) or not any(
            r["data"]["role"] == "response" for r in assets
        ):
            raise JobInputError("请上传原题与学生已批改答卷")
        result["material_snapshot"] = [{"id": r["id"], **copy.deepcopy(r["data"])} for r in assets]
        for asset in result["material_snapshot"]:
            if _sha(private_file(settings, asset["path"])) != asset["sha256"]:
                raise JobInputError("上传材料发生变化，请重新上传")
        if kind == "correction":
            if not data.get("analysis_path"):
                raise JobInputError("尚无可修正的分析")
            result["analysis_revision"] = data["analysis_revision"]
            result["analysis_sha256"] = _sha(private_file(settings, data["analysis_path"]))
            result["provider"] = data.get("provider", settings.model_provider)
    elif kind == "practice":
        progress = _progress(student, curriculum)
        if not progress.learned_point_ids:
            raise JobInputError("请先设置学生教学进度")
        request = PracticeRequest(
            quotas=data["quotas"],
            seed=data.get("seed", 0),
            difficulty_version=DIFFICULTY_VERSION,
            excluded_question_ids=data.get("excluded_question_ids", []),
        )
        if sum(q.count for q in request.quotas) == 0:
            raise JobInputError("练习题数须大于零")
        CurriculumIndex(curriculum).validate_ids([q.point_id for q in request.quotas])
        result["request"] = request.model_dump(mode="json")
        from app.admin_bank import effective_settings, state

        bank_state = state(store)
        active = effective_settings(store, settings, snapshot=bank_state)
        result["bank_revision"] = bank_state["revision"]
        result["bank_sources_sha256"] = {
            "bank": _sha(active.bank_dir / "bank.json") if active.bank_dir else None,
            "semantic": _sha(active.semantic_dir / "annotations.json")
            if active.semantic_dir
            else None,
        }
        _bank(settings, store)
        result["analysis_snapshots"] = []
        for exam_id in data.get("exam_ids", []):
            exam = store.get_record("exam", exam_id, owner)
            if exam is None or exam["data"]["student_id"] != student["id"]:
                raise JobInputError("考试与学生不匹配")
            ed = exam["data"]
            if not ed.get("analysis_path"):
                raise JobInputError("所选考试尚无分析结果")
            result["analysis_snapshots"].append(
                {
                    "exam_id": exam_id,
                    "record_revision": exam["revision"],
                    "revision": ed["analysis_revision"],
                    "path": ed["analysis_path"],
                    "sha256": _sha(private_file(settings, ed["analysis_path"])),
                }
            )
    else:
        raise JobInputError("unsupported job kind")
    return result


def _verify_inputs(store, settings, job):
    frozen = job["input_data"]
    kind = _resource_kind(job["kind"])
    resource = store.get_record(kind, job["resource_id"], job["owner_id"])
    if resource is None or resource["revision"] != frozen["resource_revision"]:
        raise JobInputError("考试或练习版本已变化，请刷新后重新提交")
    if _sha(settings.curriculum_path) != frozen["curriculum_sha256"]:
        raise JobInputError("考点目录已变化，请重新提交")
    if job["kind"] in {"practice", "bank_import"}:
        from app.admin_bank import effective_settings, state

        bank_state = state(store)
        # Pre-management jobs can only refer to the untouched initial seed.
        if bank_state["revision"] != frozen.get("bank_revision", 1):
            raise JobInputError("题库已变化，请刷新后重新提交")
        settings = effective_settings(store, settings, snapshot=bank_state)
    if job["kind"] == "bank_import":
        for file in frozen["files"]:
            if _sha(private_file(settings, file["path"])) != file["sha256"]:
                raise JobInputError("题库上传文件已变化，请重新上传")
        return
    if job["kind"] == "practice":
        student = _student(store, job["owner_id"], frozen["student_id"])
        progress = _progress(student, _curriculum(settings))
        if (
            progress.revision != frozen["progress_revision"]
            or progress.learned_point_ids != frozen["learned_point_ids"]
        ):
            raise JobInputError("教学进度已更新，请按当前进度重新生成")
        expected = frozen["bank_sources_sha256"]
        if expected != {
            "bank": _sha(settings.bank_dir / "bank.json"),
            "semantic": _sha(settings.semantic_dir / "annotations.json"),
        }:
            raise JobInputError("题库已变化，请重新生成")
        for row in frozen["analysis_snapshots"]:
            current = store.get_record("exam", row["exam_id"], job["owner_id"])
            if current is None or current["data"].get("analysis_revision") != row["revision"]:
                raise JobInputError("考试分析已更新，请重新生成")
            if _sha(private_file(settings, row["path"])) != row["sha256"]:
                raise JobInputError("考试分析文件已变化")
    else:
        rows = store.list_records("asset", job["owner_id"], job["resource_id"])
        current = {r["id"]: r["data"] for r in rows}
        frozen_assets = frozen["material_snapshot"]
        if set(current) != {a["id"] for a in frozen_assets}:
            raise JobInputError("考试材料已变化，请重新分析")
        for asset in frozen_assets:
            if current[asset["id"]] != {k: v for k, v in asset.items() if k != "id"}:
                raise JobInputError("考试材料顺序或版本已变化")
            if _sha(private_file(settings, asset["path"])) != asset["sha256"]:
                raise JobInputError("考试材料文件已变化")


def _subprocess_run(args, output, timeout=3600, *, env=None):
    with (output / "pipeline.log").open("w") as log:
        result = subprocess.run(
            args,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=env,
        )
    if result.returncode:
        raise JobInputError("试卷分析未完成，请检查材料和所选模型配置后重试；详细日志已私有保存")


def _generate_teacher_report(analysis, settings, frozen, directory, *, store=None, job=None):
    from app.codex_cli import CodexCLIConfig
    from app.exam_models import CodexExamAdapter

    provider = frozen.get("provider", settings.model_provider)
    if provider == "codex":
        if not settings.allow_codex or not settings.codex_model:
            raise JobInputError("Codex开发验证未启用")
        adapter = CodexExamAdapter(
            CodexCLIConfig(
                executable=settings.codex_executable,
                model=settings.codex_model,
                timeout_seconds=settings.model_timeout_seconds,
            )
        )
    elif provider == "deepseek":
        from app.model_runtime import BillingContext, MeteredExamAdapter

        if store is None or job is None:
            raise JobInputError("模型报告缺少有效计费上下文")
        adapter = MeteredExamAdapter(
            store=store, settings=settings, context=BillingContext.from_job(job)
        )
    else:
        raise JobInputError("不支持的模型通路")
    curriculum = _curriculum(settings)
    prompt = {
        "task": "report",
        "instructions": build_instructions("report", frozen["prompt_snapshot"]),
        "curriculum": [{"id": p.id, "name": p.name} for p in curriculum.points],
        "source_data": {"analysis": analysis.model_dump(mode="json")},
    }
    result = asyncio.run(
        adapter.assess(task="report", prompt=json.dumps(prompt, ensure_ascii=False), image_paths=[])
    )
    _write(
        directory / "report-call.json",
        {
            "provider": result.provider,
            "model": result.model,
            "prompt": prompt,
            "raw_json_result": result.raw_json_result,
            "usage": result.usage,
            "elapsed_seconds": result.elapsed_seconds,
        },
    )
    report = ExamModelResponse.model_validate(result.raw_json_result).teacher_report
    if report is None:
        raise JobInputError("模型未返回报告")
    expected = {q.number for q in analysis.questions}
    allowed = {p.id for p in curriculum.points}
    for item in report.strengths + report.priorities:
        if not set(item.question_numbers) <= expected or not set(item.point_ids) <= allowed:
            raise JobInputError("报告引用了不存在的题号或考点")
    path = directory / "teacher-report.json"
    _write(path, report.model_dump(mode="json"))
    return path, report.model_dump(mode="json")


def restore_manual_corrections(analysis, corrections, *, intake, curriculum):
    """Replay ordered teacher history, including repeated edits of one question."""
    from app.exam_analysis import apply_exam_corrections

    for correction in corrections:
        current = ExamCorrection.model_validate({**correction, "base_revision": analysis.revision})
        analysis = apply_exam_corrections(analysis, [current], intake=intake, curriculum=curriculum)
    return analysis


def _analysis(store, settings, job, directory):
    from app.exam_intake import prepare_exam_intake

    frozen = job["input_data"]
    data = frozen["resource_data"]
    provider = frozen.get("provider", settings.model_provider)
    if provider == "codex" and (not settings.allow_codex or not settings.codex_model):
        raise JobInputError("Codex开发验证未显式启用或模型未配置")
    if provider not in {"codex", "deepseek"}:
        raise JobInputError("不支持的模型通路")
    assets = frozen["material_snapshot"]
    intake_dir = directory / "intake"
    prepare_exam_intake(
        exam_id=job["resource_id"],
        student_id=frozen["student_id"],
        expected_question_numbers=list(range(1, data["question_count"] + 1)),
        paper_paths=[private_file(settings, a["path"]) for a in assets if a["role"] == "paper"],
        response_paths=[
            private_file(settings, a["path"]) for a in assets if a["role"] == "response"
        ],
        reference_paths=[
            private_file(settings, a["path"]) for a in assets if a["role"] == "reference"
        ],
        output_dir=intake_dir,
        dpi=300,
    )
    prompts = directory / "prompts.json"
    _write(prompts, frozen["prompt_snapshot"])
    run = directory / "run"
    args = [
        sys.executable,
        str(ROOT / "scripts/analyze_student_exam.py"),
        "run",
        "--intake",
        str(intake_dir / "intake.json"),
        "--curriculum",
        str(settings.curriculum_path),
        "--output",
        str(run),
        "--provider",
        provider,
        "--prompt-snapshot",
        str(prompts),
        "--teacher-report",
        "--timeout",
        str(settings.model_timeout_seconds),
    ]
    if provider == "codex":
        args += [
            "--codex-model",
            settings.codex_model,
            "--codex-executable",
            settings.codex_executable,
        ]
    if data.get("exam_date"):
        args += ["--exam-date", data["exam_date"]]
    if provider == "deepseek":
        from app.model_runtime import BillingContext

        context = BillingContext.from_job(job)
        context.check_job(store)
        _subprocess_run(args, directory, env=context.environment(settings))
    else:
        _subprocess_run(args, directory)
    result = json.loads((run / "run.json").read_text())
    analysis = ExamAnalysis.model_validate_json(Path(result["analysis_path"]).read_bytes())
    if data.get("manual_corrections"):
        from app.exam_contracts import ExamIntake

        analysis = restore_manual_corrections(
            analysis,
            data["manual_corrections"],
            intake=ExamIntake.model_validate_json(
                (intake_dir / "intake-with-regions.json").read_bytes()
            ),
            curriculum=_curriculum(settings),
        )
        path = directory / "analysis-corrected.json"
        _write(path, analysis.model_dump(mode="json"))
        result["analysis_path"] = str(path)
        if provider == "deepseek":
            report_path, _ = _generate_teacher_report(
                analysis, settings, frozen, directory, store=store, job=job
            )
        else:
            report_path, _ = _generate_teacher_report(analysis, settings, frozen, directory)
        result["teacher_report_path"] = str(report_path)
        result["unresolved_question_count"] = sum(
            q.outcome == "unknown" for q in analysis.questions
        )
        result["status"] = "partial" if result["unresolved_question_count"] else "completed"
    updated = copy.deepcopy(data)
    updated.update(
        analysis_path=_relative(settings, result["analysis_path"]),
        teacher_report_path=_relative(settings, result["teacher_report_path"])
        if result.get("teacher_report_path")
        else None,
        teacher_report=json.loads(Path(result["teacher_report_path"]).read_text())
        if result.get("teacher_report_path")
        else None,
        analysis_revision=analysis.revision,
        analysis_summary={
            "question_count": len(analysis.questions),
            "unresolved_question_count": result["unresolved_question_count"],
        },
        intake_path=_relative(settings, intake_dir / "intake-with-regions.json"),
        asset_root=_relative(settings, intake_dir),
        provider=provider,
        status=result["status"],
        prompt_revisions={k: v["revision"] for k, v in frozen["prompt_snapshot"].items()},
    )
    return (
        {
            "analysis_revision": analysis.revision,
            "unresolved_question_count": result["unresolved_question_count"],
        },
        updated,
        result["status"],
    )


def _correction(store, settings, job, directory):
    from app.exam_analysis import apply_exam_corrections
    from app.exam_contracts import ExamIntake

    frozen = job["input_data"]
    data = frozen["resource_data"]
    if _sha(private_file(settings, data["analysis_path"])) != frozen["analysis_sha256"]:
        raise JobInputError("基准分析已变化")
    base = ExamAnalysis.model_validate_json(
        private_file(settings, data["analysis_path"]).read_bytes()
    )
    current = store.get_record("exam", job["resource_id"], job["owner_id"])
    if current["data"].get("analysis_revision") != frozen["analysis_revision"]:
        raise JobInputError("已有更新的分析，请刷新后重试")
    analysis = apply_exam_corrections(
        base,
        [ExamCorrection.model_validate(c) for c in frozen["corrections"]],
        intake=ExamIntake.model_validate_json(
            private_file(settings, data["intake_path"]).read_bytes()
        ),
        curriculum=_curriculum(settings),
    )
    path = directory / "analysis.json"
    _write(path, analysis.model_dump(mode="json"))
    updated = copy.deepcopy(data)
    updated.update(
        analysis_path=_relative(settings, path),
        analysis_revision=analysis.revision,
        teacher_report_path=None,
        teacher_report=None,
        status="partial",
        analysis_summary={
            "question_count": len(analysis.questions),
            "report_requires_refresh": True,
        },
    )
    updated["manual_corrections"] = data.get("manual_corrections", []) + frozen["corrections"]
    warning = None
    try:
        if frozen.get("provider", settings.model_provider) == "deepseek":
            report_path, report = _generate_teacher_report(
                analysis, settings, frozen, directory, store=store, job=job
            )
        else:
            report_path, report = _generate_teacher_report(analysis, settings, frozen, directory)
        updated.update(teacher_report_path=_relative(settings, report_path), teacher_report=report)
        updated["analysis_summary"]["report_requires_refresh"] = False
    except Exception:
        warning = "修正已保存，报告生成失败；旧报告已失效，需重新生成"
    return (
        {
            "analysis_revision": analysis.revision,
            "warning": warning,
        },
        updated,
        "partial",
    )


def _preview(snapshot, bank_dir, curriculum):
    from lxml import html as lh

    from app.bank_view import _safe_fragment

    labels = {p.id: p.name for p in curriculum.points}
    items = []
    for item in snapshot.items:

        def blocks(fields):
            fragments = []
            for field in fields:
                for block in item.question.source_payload.get(field, []):
                    value = _safe_fragment(block.get("html", ""))
                    wrapper = lh.fragment_fromstring(value or "<p></p>", create_parent="div")
                    for image in wrapper.xpath(".//img"):
                        relative = image.get("src", "")
                        path = (bank_dir / relative).resolve(strict=True)
                        if not path.is_relative_to(bank_dir.resolve()) or _sha(
                            path
                        ) != item.question.asset_hashes.get(relative):
                            raise JobInputError("题目预览图片来源已变化")
                        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                        image.set(
                            "src",
                            f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode(),
                        )
                    fragments.append(lh.tostring(wrapper, encoding="unicode"))
            return "".join(fragments)

        items.append(
            {
                "order": item.order,
                "question_id": item.question.id,
                "target_point_id": item.target_point_id,
                "point_name": labels.get(item.target_point_id, item.target_point_id),
                "difficulty": item.question.difficulty,
                "question_html": blocks(["stem_blocks"]),
                "answer_html": blocks(["answer_blocks", "analysis_blocks"]),
            }
        )
    return {"items": items}


def _practice(store, settings, job, directory):
    from app.admin_bank import effective_settings
    from app.exporting import ExportError
    from app.source_exporting import export_bank_snapshot

    settings = effective_settings(store, settings)
    frozen = job["input_data"]
    curriculum = _curriculum(settings)
    student = _student(store, job["owner_id"], frozen["student_id"])
    bank = _bank(settings, store)
    snapshot = build_bank_practice(
        bank,
        curriculum,
        _progress(student, curriculum),
        PracticeRequest.model_validate(frozen["request"]),
        is_synthetic_student=student["data"].get("is_synthetic_student", False) is True,
    )
    warnings = []
    if frozen["analysis_snapshots"]:
        warnings.append(
            "所选考试原题与共享题库尚无全部已确认的同题身份，请预览核对；已明确选择的排除题目由程序过滤"
        )
    preview = _preview(snapshot, settings.bank_dir, curriculum)
    _write(directory / "selection.json", snapshot.model_dump(mode="json"))
    updated = copy.deepcopy(frozen["resource_data"])
    updated.update(
        snapshot=snapshot.model_dump(mode="json"),
        preview=preview,
        warnings=warnings,
        progress_revision=frozen["progress_revision"],
    )
    if snapshot.shortfalls:
        updated.update(status="partial", files=[])
        return (
            {
                "selected": len(snapshot.items),
                "shortfalls": [s.model_dump(mode="json") for s in snapshot.shortfalls],
            },
            updated,
            "partial",
        )
    pdf_engine = updated.get("pdf_engine", "latex")
    if pdf_engine == "latex" and not settings.xelatex_executable:
        raise JobInputError("LaTeX排版尚未配置XeLaTeX，请配置后重试或明确选择Word模板转换")
    _verify_inputs(store, settings, job)
    result = export_bank_snapshot(
        snapshot,
        directory / "word",
        bank_dir=settings.bank_dir,
        practice_bank=bank,
        curriculum=curriculum,
        current_progress=_progress(student, curriculum),
        soffice_executable=settings.soffice_executable,
        layout=updated.get("layout", "answer_space"),
    )
    files = result["files"]
    if pdf_engine == "latex":
        from app.latex_exporting import export_latex_snapshot
        from app.math_latex import build_formula_catalog

        try:
            latex = export_latex_snapshot(
                snapshot,
                directory / "latex",
                bank_dir=settings.bank_dir,
                practice_bank=bank,
                curriculum=curriculum,
                current_progress=_progress(student, curriculum),
                formula_catalog=build_formula_catalog(snapshot, settings.bank_dir),
                xelatex_executable=settings.xelatex_executable,
                layout=updated.get("layout", "answer_space"),
            )
        except ExportError as exc:
            raise JobInputError(
                "所选题的LaTeX转换或排版失败，未发布文件；可更换题目或明确选择Word模板转换后重新生成"
            ) from exc
        files.update(
            student_pdf=latex["files"]["student_pdf"], teacher_pdf=latex["files"]["teacher_pdf"]
        )
    else:
        warnings.append("本次按所选Word模板转换生成PDF")
    labels = {
        "student_docx": "学生练习卷 Word",
        "student_pdf": "学生练习卷 PDF",
        "teacher_docx": "教师答案 Word",
        "teacher_pdf": "教师答案 PDF",
    }
    updated.update(
        status="completed",
        files=[
            {
                "id": key,
                "name": key,
                "label": labels[key],
                "path": _relative(settings, value),
                "sha256": _sha(value),
                "filename": key + Path(value).suffix,
            }
            for key, value in files.items()
            if key in labels
        ],
    )
    return (
        {"selected": len(snapshot.items), "file_count": len(updated["files"])},
        updated,
        "completed",
    )


def _resource_kind(kind):
    return {"practice": "practice", "bank_import": "bank_import"}.get(kind, "exam")


def _bank_import(store, settings, job, directory):
    from app.admin_bank import effective_settings, prepare_import_publication
    from app.admin_bank_ingest import process_import

    active = effective_settings(store, settings)
    result = process_import(store, settings, job, directory, base_bank_dir=active.bank_dir)
    publication = prepare_import_publication(
        store, settings, result["package_path"], job["input_data"]["bank_revision"],
        job["owner_id"], job["input_data"]["reason"],
    )
    public_result = {k: v for k, v in result.items() if k != "package_path"}
    public_result["_bank_publication"] = publication
    data = {**copy.deepcopy(job["input_data"]), "status": "completed"}
    return public_result, data, "completed"


def execute_job(store, settings, job):
    _verify_inputs(store, settings, job)
    if not all(
        re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", str(job[key])) for key in ("id", "lease_token")
    ):
        raise JobInputError("invalid task identity")
    directory = settings.data_dir / "jobs" / job["id"] / job["lease_token"]
    directory.mkdir(parents=True, exist_ok=False)
    handler = {"analysis": _analysis, "practice": _practice,
               "correction": _correction, "bank_import": _bank_import}[job["kind"]]
    result, data, status = handler(store, settings, job, directory)
    _verify_inputs(store, settings, job)
    return result, data, status


def run_once(store, settings, worker_id):
    job = store.claim_job(worker_id, lease_seconds=900)
    if job is None:
        return False
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(30):
            if not store.heartbeat_job(job["id"], job["lease_token"], lease_seconds=900):
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    kind = _resource_kind(job["kind"])
    record = store.get_record(kind, job["resource_id"], job["owner_id"])
    try:
        result, data, status = execute_job(store, settings, job)
        bank_publication = result.pop("_bank_publication", None)
        # Finish and pointer publication share one DB transaction and lease CAS.
        guards = []
        if job["kind"] == "practice":
            frozen = job["input_data"]
            from app.admin_bank import STATE_ID, STATE_OWNER

            guards.append({
                "kind": "bank_state", "record_id": STATE_ID, "owner_id": STATE_OWNER,
                "revision": frozen.get("bank_revision", 1),
            })
            guards.append(
                {
                    "kind": "student",
                    "record_id": frozen["student_id"],
                    "owner_id": job["owner_id"],
                    "revision": frozen["student_record_revision"],
                }
            )
            guards.extend(
                {
                    "kind": "exam",
                    "record_id": row["exam_id"],
                    "owner_id": job["owner_id"],
                    "revision": row["record_revision"],
                }
                for row in frozen["analysis_snapshots"]
            )
        published = store.finish_job(
            job["id"],
            job["lease_token"],
            result,
            status=status,
            resource_update={
                "kind": kind,
                "record_id": record["id"],
                "owner_id": job["owner_id"],
                "data": data,
                "expected_revision": job["input_data"]["resource_revision"],
            },
            guards=guards,
            additional_resource_updates=[bank_publication] if bank_publication else None,
        )
        if not published:
            store.finish_job(
                job["id"],
                job["lease_token"],
                None,
                status="failed",
                error="资料、教学进度或题库版本已更新，本次结果未发布，请刷新后重新提交",
            )
    except Exception as exc:
        # Detailed diagnostics stay private; never return provider keys or raw text.
        if record is not None:
            location = settings.data_dir / "job-errors"
            location.mkdir(parents=True, exist_ok=True)
            diagnostic = traceback.format_exc()
            for key in ("DEEPSEEK_API_KEY", "TEASSISTANT_DATABASE_URL", "TEASSISTANT_DB_PASSWORD"):
                if os.getenv(key):
                    diagnostic = diagnostic.replace(os.environ[key], "[REDACTED]")
            (location / (job["id"] + ".log")).write_text(diagnostic)
        from app.admin_bank import BankError
        from app.admin_bank_ingest import AdminBankImportError
        from app.model_billing import BillingError
        from app.model_runtime import MeteringError
        from app.model_settings import ModelSettingsError

        public = (
            str(exc)
            if isinstance(exc, (JobInputError, BillingError, MeteringError, ModelSettingsError,
                                BankError, AdminBankImportError))
            and not any(s in str(exc) for s in ("/Users/", "sk-", "Bearer ", "api_key"))
            else "任务处理失败，请检查模型或导出配置后重试"
        )
        store.finish_job(job["id"], job["lease_token"], None, status="failed", error=public[:500])
    finally:
        stop.set()
        thread.join(timeout=1)
    return True
