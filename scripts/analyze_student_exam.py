"""Private exam intake and explicit LLM development analysis; never publishes bank items."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts import Curriculum  # noqa: E402
from app.exam_contracts import ExamIntake, ExamModelResponse  # noqa: E402
from app.semantic_bank import canonical_hash  # noqa: E402

PROMPT_VERSION = "exam-analysis-v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, data) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def bound(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("exam asset escapes intake root")
    return path


def validate_evidence(
    payload: ExamModelResponse,
    intake: ExamIntake,
    task: str,
    allowed: set[str],
    *,
    input_page_ids: set[str] | None = None,
    question_numbers: set[int] | None = None,
    upstream_evidence: list[dict] | None = None,
    paper_evidence_by_number: dict[int, list[dict]] | None = None,
):
    pages = {p.id: p for p in intake.pages}
    input_page_ids = set(pages) if input_page_ids is None else input_page_ids
    upstream = {
        (item["page_id"], tuple(item["bbox"]), item["kind"]) for item in (upstream_evidence or [])
    }

    def visit(value, maximum_context=False):
        if isinstance(value, dict):
            maximum_context = maximum_context or value.get("kind") in {"maximum", "total_maximum"}
            if "page_id" in value and "bbox" in value:
                page = pages.get(value["page_id"])
                if page is None:
                    raise ValueError("model evidence refers to an unknown page")
                kind = value.get("kind")
                claim = (value["page_id"], tuple(value["bbox"]), kind)
                if value["page_id"] not in input_page_ids and claim not in upstream:
                    raise ValueError("model evidence was not provided to this call")
                role = {
                    "printed_question": "paper",
                    "handwriting": "response",
                    "grading": "response",
                    "reference": "reference",
                }.get(kind)
                if kind == "grading" and maximum_context:
                    if page.role not in {"paper", "response", "reference"}:
                        raise ValueError("maximum source role is invalid")
                elif role is not None and page.role != role:
                    raise ValueError("model evidence has the wrong material role")
            for child in value.values():
                visit(child, maximum_context)
        elif isinstance(value, list):
            for child in value:
                visit(child, maximum_context)

    for response in payload.responses:
        permitted_paper = (
            upstream
            if paper_evidence_by_number is None
            else {
                (item["page_id"], tuple(item["bbox"]), item["kind"])
                for item in paper_evidence_by_number.get(response.number, [])
            }
        )
        for source in response.paper_sources:
            claim = (source.page_id, tuple(source.bbox), source.kind)
            page = pages.get(source.page_id)
            if (
                source.kind != "printed_question"
                or page is None
                or page.role != "paper"
                or claim not in permitted_paper
            ):
                raise ValueError(
                    "response paper_sources not provided to this call as verified upstream paper evidence"
                )
    visit(payload.model_dump(mode="json"))
    if payload.task != task:
        raise ValueError("model returned a different analysis stage")
    permitted = {
        "layout": {"regions"},
        "paper": {"questions", "scoring_rules", "grading"},
        "reference": {"reference_answers"},
        "response": {"responses", "grading"},
        "diagnosis": {"diagnoses"},
        "report": {"teacher_report"},
    }[task]
    lists = {
        "regions",
        "questions",
        "scoring_rules",
        "responses",
        "grading",
        "diagnoses",
        "reference_answers",
        "teacher_report",
    }
    for name in lists - permitted:
        if getattr(payload, name):
            raise ValueError("model mixed results from different stages")
    expected = set(intake.expected_question_numbers)
    if task == "report":
        if payload.teacher_report is None:
            raise ValueError("report stage returned no teacher report")
        for item in payload.teacher_report.strengths + payload.teacher_report.priorities:
            if not set(item.question_numbers) <= expected or not set(item.point_ids) <= allowed:
                raise ValueError("teacher report invented a question or curriculum identifier")
    requested = expected if question_numbers is None else question_numbers
    for collection in (
        payload.questions,
        payload.responses,
        payload.diagnoses,
        payload.reference_answers,
    ):
        for item in collection:
            if item.number not in expected or item.number not in requested:
                raise ValueError("model invented an unexpected question number")
            if not set(getattr(item, "point_ids", [])) <= allowed:
                raise ValueError("model used unknown curriculum identifiers")
    for rule in payload.scoring_rules:
        if not set(rule.question_numbers) <= expected:
            raise ValueError("score rule refers to unexpected questions")
    for region in payload.regions:
        if (
            region.page_id not in input_page_ids
            or region.page_id not in pages
            or pages[region.page_id].role != region.role
        ):
            raise ValueError("layout region role differs")
        if not set(region.question_numbers) <= expected:
            raise ValueError("layout region invents question numbers")


def instructions(task: str, snapshot=None) -> str:
    from app.prompt_templates import build_instructions

    return build_instructions(task, snapshot)


async def run(args):
    from app.codex_cli import CodexCLIConfig
    from app.exam_models import CodexExamAdapter, DeepSeekExamAdapter

    original_bytes = args.intake.read_bytes()
    intake = ExamIntake.model_validate_json(original_bytes)
    root = args.intake.resolve().parent
    curriculum_bytes = args.curriculum.read_bytes()
    curriculum = Curriculum.model_validate_json(curriculum_bytes)
    prompt_file = getattr(args, "prompt_snapshot", None)
    prompt_snapshot = json.loads(prompt_file.read_bytes()) if prompt_file else None
    if prompt_snapshot is not None:
        for task in ("layout", "paper", "reference", "response", "diagnosis", "report"):
            instructions(task, prompt_snapshot)
    output = args.output.absolute()
    if output.resolve().is_relative_to(root) or root.is_relative_to(output.resolve()):
        raise ValueError("analysis output must be separate from intake assets")
    allowed = {p.id for p in curriculum.points}
    model = args.codex_model if args.provider == "codex" else None
    executable_identity = None
    if args.provider == "codex":
        if not model:
            raise ValueError("Codex development analysis requires an explicit model")
        located = shutil.which(args.codex_executable)
        if located is None:
            raise ValueError("configured Codex executable is unavailable")
        executable = Path(located).resolve(strict=True)
        executable_identity = {"path": str(executable), "sha256": digest(executable)}
        adapter = CodexExamAdapter(
            CodexCLIConfig(executable=str(executable), model=model, timeout_seconds=args.timeout)
        )
    else:
        from app.model_runtime import CONTEXT_ENV, REQUIRED_ENV, adapter_from_environment

        if os.getenv(REQUIRED_ENV) == "1" or os.getenv(CONTEXT_ENV):
            adapter = adapter_from_environment(timeout=args.timeout)
            model = adapter.config.model
        else:
            from app.ai import DeepSeekConfig

            config = DeepSeekConfig.from_env()
            model = config.model
            adapter = DeepSeekExamAdapter(config)
    identity = {
        "schema_version": "exam-run-v1",
        "prompt_version": PROMPT_VERSION,
        "intake_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "curriculum_sha256": hashlib.sha256(curriculum_bytes).hexdigest(),
        "response_schema_sha256": canonical_hash(ExamModelResponse.model_json_schema()),
        "provider": args.provider,
        "model": model,
        "codex_executable": executable_identity,
        "exam_date": args.exam_date,
    }
    if prompt_snapshot is not None:
        from app.prompt_templates import prompt_digest

        identity["prompt_snapshot_sha256"] = prompt_digest(prompt_snapshot)
    output.mkdir(parents=True, exist_ok=True)
    identity_path = output / "identity.json"
    if identity_path.exists():
        if not args.resume or json.loads(identity_path.read_text()) != identity:
            raise ValueError("run already exists or identity changed; use a new output")
    elif any(output.iterdir()):
        raise ValueError("analysis output must be empty for a new run")
    else:
        save(identity_path, identity)
    calls = output / "calls"
    calls.mkdir(exist_ok=True)
    semaphore = asyncio.Semaphore(args.concurrency)
    failures = []

    async def call(task, pages, data):
        for page in pages:
            path = bound(root, page.image_path)
            if digest(path) != page.image_sha256:
                raise ValueError("input page changed")
        context = {
            "task": task,
            "instructions": instructions(task, prompt_snapshot),
            "images": [
                {"page_id": p.id, "role": p.role, "width": p.width, "height": p.height}
                for p in pages
            ],
            "expected_question_numbers": intake.expected_question_numbers,
            "curriculum": [{"id": p.id, "name": p.name} for p in curriculum.points]
            if task in {"paper", "diagnosis", "report"}
            else [],
            "source_data": data,
        }
        upstream_evidence = [
            item for question in data.get("paper_questions", []) for item in question["sources"]
        ] + [item for answer in data.get("reference_answers", []) for item in answer["sources"]]
        scope = {
            "input_page_ids": {p.id for p in pages},
            "question_numbers": set(data["question_numbers"])
            if "question_numbers" in data
            else None,
            "upstream_evidence": upstream_evidence,
            "paper_evidence_by_number": {
                question["number"]: question["sources"]
                for question in data.get("paper_questions", [])
            },
        }
        prompt = json.dumps(context, ensure_ascii=False)
        key = canonical_hash(
            {"identity": identity, "prompt": prompt, "images": [p.image_sha256 for p in pages]}
        )
        path = calls / f"{key}.json"
        if path.exists():
            record = json.loads(path.read_text())
            if record["status"] == "succeeded":
                result = ExamModelResponse.model_validate(record["raw_json_result"])
                validate_evidence(result, intake, task, allowed, **scope)
                return result
        if path.exists():
            archive = output / "failed-attempts"
            archive.mkdir(exist_ok=True)
            index = len(list(archive.glob(f"{key}-*.json"))) + 1
            save(archive / f"{key}-{index}.json", json.loads(path.read_text()))
        record = {
            "task": task,
            "key": key,
            "status": "running",
            "input_page_ids": [p.id for p in pages],
            "prompt": context,
        }
        save(path, record)
        try:
            async with semaphore:
                result = await adapter.assess(
                    task=task,
                    prompt=prompt,
                    image_paths=[bound(root, p.image_path) for p in pages],
                )
            record.update(
                {
                    "provider": result.provider,
                    "model": result.model,
                    "attempts": result.attempts,
                    "usage": result.usage,
                    "elapsed_seconds": result.elapsed_seconds,
                    "raw_json_result": result.raw_json_result,
                }
            )
            parsed = ExamModelResponse.model_validate(result.raw_json_result)
            validate_evidence(parsed, intake, task, allowed, **scope)
            record["status"] = "succeeded"
            save(path, record)
            print(f"{task}: succeeded ({len(pages)} image(s))", flush=True)
            return parsed
        except Exception as exc:
            record.update(
                status="failed", error_code=getattr(exc, "error_code", type(exc).__name__)
            )
            for name in ("raw_response", "raw_json_result", "attempts", "usage", "diagnostics"):
                if getattr(exc, name, None) is not None:
                    record[name] = getattr(exc, name)
            save(path, record)
            failures.append({"task": task, "call": key, "error_code": record["error_code"]})
            print(f"{task}: failed ({record['error_code']})", flush=True)
            return None

    response_pages = [p for p in intake.pages if p.role == "response" and p.parent_page_id is None]
    layouts = []
    for page in response_pages:
        with Image.open(bound(root, page.image_path)) as image:
            extrema = image.convert("RGB").getextrema()
        if all(low == high == 255 for low, high in extrema):
            layouts.append(ExamModelResponse(task="layout", page_classification="blank"))
        else:
            result = await call("layout", [page], {})
            if result is not None:
                layouts.append(result)
    if args.stage == "layout":
        save(output / "layout.json", [result.model_dump(mode="json") for result in layouts])
        save(
            output / "run.json",
            {
                **identity,
                "status": "layout_partial" if failures else "layout_completed",
                "failures": failures,
            },
        )
        print("Layout-only stage saved; use --resume for full analysis", flush=True)
        return
    from app.exam_intake import crop_exam_regions

    regions = [region for result in layouts for region in result.regions]
    if regions:
        intake = crop_exam_regions(intake, regions, root)
    save(output / "intake-with-regions.json", intake.model_dump(mode="json"))
    child_parents = {p.parent_page_id for p in intake.pages if p.parent_page_id is not None}
    blank_ids = set()
    for page in response_pages:
        with Image.open(bound(root, page.image_path)) as image:
            if all(low == high == 255 for low, high in image.convert("RGB").getextrema()):
                blank_ids.add(page.id)
    response_views = [
        p
        for p in intake.pages
        if p.role == "response" and p.id not in child_parents and p.id not in blank_ids
    ]
    paper_pages = [p for p in intake.pages if p.role == "paper" and p.parent_page_id is None]
    paper = [
        r
        for r in await asyncio.gather(*[call("paper", [p], {}) for p in paper_pages])
        if r is not None
    ]
    reference_pages = [
        p for p in intake.pages if p.role == "reference" and p.parent_page_id is None
    ]
    references = [
        r
        for r in await asyncio.gather(*[call("reference", [p], {}) for p in reference_pages])
        if r is not None
    ]
    paper_data = [q.model_dump(mode="json") for result in paper for q in result.questions]
    ref_data = [
        q.model_dump(mode="json") for result in references for q in result.reference_answers
    ]

    async def read_response(page):
        matched_regions = [
            region
            for region in regions
            if region.page_id == page.parent_page_id
            and page.parent_bbox is not None
            and all(abs(a - b) < 0.002 for a, b in zip(region.bbox, page.parent_bbox))
        ]
        numbers = sorted({n for region in matched_regions for n in region.question_numbers})
        if not numbers:
            numbers = intake.expected_question_numbers
        questions = [q for q in paper_data if q["number"] in numbers]
        paper_ids = {source["page_id"] for question in questions for source in question["sources"]}
        original_pages = [p for p in intake.pages if p.role == "paper" and p.id in paper_ids]
        return await call(
            "response",
            [page, *original_pages],
            {
                "response_page_id": page.id,
                "question_numbers": numbers,
                "paper_questions": questions,
                "reference_answers": [q for q in ref_data if q["number"] in numbers],
                "paper_titles": [r.exam_title for r in paper if r.exam_title],
            },
        )

    response = [
        result
        for result in await asyncio.gather(*[read_response(page) for page in response_views])
        if result is not None
    ]
    from app.exam_analysis import analyze_exam

    provisional = analyze_exam(
        intake=intake,
        paper_results=paper,
        response_results=response,
        diagnosis_results=[],
        reference_results=references,
        curriculum=curriculum,
        exam_date=args.exam_date,
    )
    targets = [
        question.number
        for question in provisional.questions
        if question.outcome in {"incorrect", "partial", "unanswered"}
    ]
    response_rows = [r for result in response for r in result.responses]
    question_map = {q["number"]: q for q in paper_data}
    diagnosis_jobs = []
    for start in range(0, len(targets), 4):
        numbers = targets[start : start + 4]
        rows = [r for r in response_rows if r.number in numbers]
        needed = {s.page_id for r in rows for s in r.sources}
        needed |= {
            s["page_id"] for n in numbers if n in question_map for s in question_map[n]["sources"]
        }
        pages = [p for p in intake.pages if p.id in needed]
        if not rows or not pages:
            continue
        diagnosis_jobs.append(
            call(
                "diagnosis",
                pages,
                {
                    "question_numbers": numbers,
                    "paper_questions": [question_map[n] for n in numbers if n in question_map],
                    "student_responses": [r.model_dump(mode="json") for r in rows],
                    "reference_answers": [r for r in ref_data if r["number"] in numbers],
                    "reference_key_provided": bool(references),
                    "step_scoring_rubric_provided": False,
                },
            )
        )
    diagnosis = [result for result in await asyncio.gather(*diagnosis_jobs) if result is not None]
    if (
        args.intake.read_bytes() != original_bytes
        or args.curriculum.read_bytes() != curriculum_bytes
    ):
        raise ValueError("source intake or curriculum changed during the run")
    from app.exam_analysis import analyze_exam, summarize_learning
    from app.exam_view import render_exam_report

    analysis = analyze_exam(
        intake=intake,
        paper_results=paper,
        response_results=response,
        diagnosis_results=diagnosis,
        reference_results=references,
        curriculum=curriculum,
        exam_date=args.exam_date,
    )
    summary = summarize_learning([analysis], student_id=intake.student_id, curriculum=curriculum)
    teacher_report_path = None
    if getattr(args, "teacher_report", False):
        result = await call(
            "report",
            [],
            {
                "analysis": analysis.model_dump(mode="json"),
                "question_numbers": [q.number for q in analysis.questions],
            },
        )
        if result is not None:
            teacher_report_path = output / f"teacher-report-{analysis.revision}.json"
            save(teacher_report_path, result.teacher_report.model_dump(mode="json"))
    analysis_path = output / f"analysis-{analysis.revision}.json"
    payload = analysis.model_dump(mode="json")
    if analysis_path.exists() and json.loads(analysis_path.read_text()) != payload:
        raise ValueError("saved analysis revision changed; refusing silent overwrite")
    save(analysis_path, payload)
    # A view change must not silently reuse an older HTML for the same analysis.
    renderer_sha256 = digest(ROOT / "backend/app/exam_view.py")
    report = output / "reports" / analysis.revision / renderer_sha256[:16]
    if report.exists():
        paths = json.loads((report / "rendered-paths.json").read_text())
        hashes = json.loads((report / "rendered-hashes.json").read_text())
        for relative, expected_hash in hashes.items():
            if digest(bound(report, relative)) != expected_hash:
                raise ValueError("saved report changed; refusing silent overwrite")
    else:
        paths = render_exam_report(
            analysis,
            intake=intake,
            asset_root=root,
            output_dir=report,
            summary=summary,
            curriculum=curriculum,
        )
        hashes = {
            str(path.relative_to(report)): digest(path)
            for path in report.rglob("*")
            if path.is_file()
        }
        save(report / "rendered-paths.json", paths)
        save(report / "rendered-hashes.json", hashes)
    save(output / "summary.json", summary.model_dump(mode="json"))
    record = {
        **identity,
        "status": "partial" if failures or summary.unresolved_question_count else "completed",
        "unresolved_question_count": summary.unresolved_question_count,
        "failures": failures,
        "expected_question_count": len(intake.expected_question_numbers),
        "recognized_paper_question_count": len({q["number"] for q in paper_data}),
        "recognized_response_question_count": len({r.number for r in response_rows}),
        "physical_page_count": sum(p.parent_page_id is None for p in intake.pages),
        "derived_view_count": sum(p.parent_page_id is not None for p in intake.pages),
        "reference_key_provided": bool(references),
        "analysis_revision": analysis.revision,
        "analysis_path": str(analysis_path),
        "renderer_sha256": renderer_sha256,
        "teacher_report_path": str(teacher_report_path) if teacher_report_path else None,
        "report": paths,
    }
    save(output / "run.json", record)
    print(
        json.dumps(
            {
                k: record[k]
                for k in (
                    "status",
                    "expected_question_count",
                    "recognized_paper_question_count",
                    "recognized_response_question_count",
                    "report",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def load_analysis_document(content):
    from app.exam_contracts import ExamAnalysis

    payload = json.loads(content)
    if isinstance(payload, dict) and payload.get("schema_version") == "exam-report-v1":
        payload = payload["analysis"]
    return ExamAnalysis.model_validate(payload)


def offline_command(args):
    """Versioned teacher correction or distinct-exam summary; no model invocation."""
    from app.exam_analysis import apply_exam_corrections, summarize_learning
    from app.exam_contracts import ExamCorrection
    from app.exam_view import render_exam_report

    curriculum = Curriculum.model_validate_json(args.curriculum.read_bytes())
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("offline output must not already exist")
    if args.command == "summary":
        analyses = [load_analysis_document(path.read_bytes()) for path in args.analyses]
        summary = summarize_learning(
            analyses,
            student_id=args.student_id,
            curriculum=curriculum,
            exam_ids=args.exam_ids,
            limit=3,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        save(args.output, summary.model_dump(mode="json"))
        print(f"Saved summary of {summary.actual_exam_count} distinct exam(s)")
        return
    original = args.analysis.read_bytes()
    analysis = load_analysis_document(original)
    intake = ExamIntake.model_validate_json(args.intake.read_bytes())
    corrections = [
        ExamCorrection.model_validate(item) for item in json.loads(args.corrections.read_text())
    ]
    updated = apply_exam_corrections(analysis, corrections, intake=intake, curriculum=curriculum)
    if args.analysis.read_bytes() != original:
        raise ValueError("base analysis changed during correction")
    summary = summarize_learning([updated], student_id=updated.student_id, curriculum=curriculum)
    paths = render_exam_report(
        updated,
        intake=intake,
        asset_root=args.asset_root or args.intake.resolve().parent,
        output_dir=args.output,
        summary=summary,
        curriculum=curriculum,
    )
    print(json.dumps({"revision": updated.revision, "report": paths}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("inputs", "output"):
        prepare.add_argument(f"--{name}", type=Path, required=True)
    prepare.add_argument("--exam-id", required=True)
    prepare.add_argument("--student-id", required=True)
    prepare.add_argument("--synthetic-student", action="store_true")
    prepare.add_argument("--dpi", type=int, default=400)
    analyze = commands.add_parser("run")
    for name in ("intake", "curriculum", "output"):
        analyze.add_argument(f"--{name}", type=Path, required=True)
    analyze.add_argument("--provider", choices=("deepseek", "codex"), required=True)
    analyze.add_argument("--codex-model", default=os.getenv("CODEX_MODEL"))
    analyze.add_argument("--codex-executable", default=os.getenv("CODEX_EXECUTABLE", "codex"))
    analyze.add_argument("--exam-date", default=None)
    analyze.add_argument("--timeout", type=float, default=360)
    analyze.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)
    analyze.add_argument("--resume", action="store_true")
    analyze.add_argument("--stage", choices=("layout", "all"), default="all")
    analyze.add_argument("--prompt-snapshot", type=Path)
    analyze.add_argument("--teacher-report", action="store_true")
    correction = commands.add_parser("correct")
    for name in ("analysis", "intake", "curriculum", "corrections", "output"):
        correction.add_argument(f"--{name}", type=Path, required=True)
    correction.add_argument("--asset-root", type=Path)
    summary = commands.add_parser("summary")
    summary.add_argument("--analyses", nargs="+", type=Path, required=True)
    summary.add_argument("--student-id", required=True)
    summary.add_argument("--curriculum", type=Path, required=True)
    summary.add_argument("--exam-ids", nargs="+")
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        from app.exam_intake import prepare_exam_intake

        inputs = json.loads(args.inputs.read_text())
        intake = prepare_exam_intake(
            exam_id=args.exam_id,
            student_id=args.student_id,
            expected_question_numbers=inputs["expected_question_numbers"],
            paper_paths=[Path(p) for p in inputs["paper"]],
            response_paths=[Path(p) for p in inputs["response"]],
            reference_paths=[Path(p) for p in inputs.get("reference", [])],
            output_dir=args.output,
            is_synthetic_student=args.synthetic_student,
            dpi=args.dpi,
        )
        print(
            json.dumps(
                {"pages": len(intake.pages), "materials": len(intake.materials)}, ensure_ascii=False
            )
        )
    elif args.command == "run":
        asyncio.run(run(args))
    else:
        offline_command(args)


if __name__ == "__main__":
    main()
