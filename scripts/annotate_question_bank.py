"""Explicit semantic trial on an immutable source bank, with resumable private evidence."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import AIAdapterError, DeepSeekConfig  # noqa: E402
from app.codex_cli import CodexCLIConfig  # noqa: E402
from app.contracts import Curriculum, difficulty_level  # noqa: E402
from app.curriculum import CurriculumIndex  # noqa: E402
from app.semantic_bank import (  # noqa: E402
    build_semantic_prompt,
    validate_assessments,
    validate_input_manifest,
)
from app.semantic_contracts import DIFFICULTY_VERSION, PROMPT_VERSION  # noqa: E402
from app.semantic_models import CodexSemanticAdapter, DeepSeekSemanticAdapter  # noqa: E402
from app.semantic_view import render_semantic_report  # noqa: E402


def write_json(path: Path, value: dict, *, exclusive: bool = False) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    else:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content)
        os.replace(temporary, path)


def codex_executable_identity(executable: str) -> dict:
    located = shutil.which(executable)
    if located is None:
        raise ValueError("configured Codex executable is unavailable")
    path = Path(located).resolve(strict=True)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "sha256": digest.hexdigest()}


def summarize(rows: dict, calls: list[dict]) -> dict:
    counts = Counter(row["status"] for row in rows.values())
    assessed = [row for row in rows.values() if row["status"] == "assessed"]
    return {
        "denominator": len(rows),
        "assessed": counts["assessed"],
        "failed": counts["failed"],
        "not_attempted": counts["not_attempted"],
        "running": counts["running"],
        "structure_valid_rate_all_questions": len(assessed) / len(rows) if rows else None,
        "difficulty_distribution": dict(
            Counter(difficulty_level(row["assessment"]["difficulty_score"]) for row in assessed)
        ),
        "target_point_counts": dict(
            Counter(point for row in assessed for point in row["assessment"]["point_ids"])
        ),
        "unverified_association_questions": sum(
            row["association_status"] != "confirmed" for row in rows.values()
        ),
        "model_calls": sum(call.get("attempts", 1) for call in calls),
        "model_calls_definition": "adapter-reported attempts; CLI internal transport retries and server executions unknown",
        "adapter_invocations": len(calls),
        "successful_response_batches": sum(
            call.get("raw_json_result") is not None and not call.get("error_code") for call in calls
        ),
        "usage_known_calls": sum(call.get("usage") is not None for call in calls),
        "practice_ready": 0,
        "semantic_accuracy": "not_measured_against_independent_gold",
        "difficulty_calibration": "not_measured_against_teacher_or_student_data",
    }


def render_results(
    output: Path, rows: dict, index: CurriculumIndex, inputs: dict, input_dir: Path
) -> None:
    render_semantic_report(output, rows, index.curriculum, inputs, input_dir)


async def run(args) -> dict:
    bank_path = args.bank.resolve(strict=True) / "bank.json"
    bank_content = bank_path.read_bytes()
    bank = json.loads(bank_content)
    input_file = args.inputs.resolve(strict=True)
    input_content = input_file.read_bytes()
    input_manifest = json.loads(input_content)
    entries = validate_input_manifest(
        bank, input_manifest, input_file.parent, hashlib.sha256(bank_content).hexdigest()
    )
    catalog_content = args.curriculum.read_bytes()
    curriculum = Curriculum.model_validate_json(catalog_content)
    if curriculum.is_synthetic:
        raise ValueError("real source banks require a non-synthetic curriculum")
    index = CurriculumIndex(curriculum)
    if args.provider == "codex":
        if not args.codex_model:
            raise ValueError("explicit --codex-model or CODEX_MODEL is required for batch trials")
        config = CodexCLIConfig(
            executable=args.codex_executable, model=args.codex_model, timeout_seconds=args.timeout
        )
        adapter = CodexSemanticAdapter(config)
        configured_model = config.model or "cli-default"
        executable_identity = codex_executable_identity(config.executable)
    else:
        config = DeepSeekConfig.from_env()
        adapter = DeepSeekSemanticAdapter(config)
        configured_model = config.model
        executable_identity = None
    metadata = {
        "schema_version": "source-semantic-run-v1",
        "source_bank_sha256": hashlib.sha256(bank_content).hexdigest(),
        "inputs_sha256": hashlib.sha256(input_content).hexdigest(),
        "curriculum_sha256": hashlib.sha256(catalog_content).hexdigest(),
        "curriculum_version": curriculum.version,
        "curriculum_status": "engineering_draft",
        "prompt_version": PROMPT_VERSION,
        "difficulty_version": DIFFICULTY_VERSION,
        "provider": args.provider,
        "configured_model": configured_model,
        "codex_executable": executable_identity,
        "mode": "development_validation" if args.provider == "codex" else "business_model_trial",
        "question_ids": list(entries),
        "practice_ready": False,
    }
    output = args.output.absolute()
    if output.exists():
        if not args.resume or json.loads((output / "run.json").read_text()) != metadata:
            raise ValueError(
                "existing run requires --resume and exactly matching source, inputs, catalog and model"
            )
        rows = json.loads((output / "annotations.json").read_text())["questions"]
        if set(rows) != set(entries):
            raise ValueError("existing annotation cohort changed")
        for row in rows.values():
            if row["status"] == "running":
                row.update(status="failed", error_code="interrupted_call_outcome_unknown")
    else:
        if args.resume:
            raise ValueError("cannot resume a missing run")
        output.mkdir(parents=True)
        (output / "calls").mkdir()
        (output / "reports").mkdir()
        write_json(output / "run.json", metadata, exclusive=True)
        with (output / "curriculum.json").open("xb") as stream:
            stream.write(catalog_content)
        rows = {
            qid: {
                "status": "not_attempted",
                "association_status": item["association_status"],
                "source_hash": item["source_hash"],
                "assessment": None,
                "required_point_ids": [],
                "error_code": None,
            }
            for qid, item in entries.items()
        }
    wanted = list(entries)
    if args.question_ids_file:
        wanted = json.loads(args.question_ids_file.read_text())
        if (
            not isinstance(wanted, list)
            or len(wanted) != len(set(wanted))
            or not set(wanted) <= set(entries)
        ):
            raise ValueError("selected question IDs differ from the source cohort")
    pending = [
        qid
        for qid in wanted
        if rows[qid]["status"] == "not_attempted"
        or (args.retry_failed and rows[qid]["status"] == "failed")
    ]
    calls = [
        json.loads(path.read_text()) for path in sorted((output / "calls").glob("*/result.json"))
    ]
    sequence = max(
        [int(path.name) for path in (output / "calls").iterdir() if path.is_dir()] or [0]
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    def checkpoint():
        write_json(output / "annotations.json", {"run": metadata, "questions": rows})
        summary = summarize(rows, calls)
        write_json(output / "summary.json", summary)
        report_id = len(list((output / "reports").glob("*.json"))) + 1
        write_json(output / "reports" / f"{report_id:04d}.json", summary, exclusive=True)

    checkpoint()

    async def batch(qids: list[str], call_id: int):
        async with semaphore:
            call_dir = output / "calls" / f"{call_id:04d}"
            call_dir.mkdir()
            selected = [entries[qid] for qid in qids]
            prompt = build_semantic_prompt(selected, index)
            write_json(
                call_dir / "request.json",
                {
                    "question_ids": qids,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
                exclusive=True,
            )
            (call_dir / "prompt.txt").write_text(prompt)
            for qid in qids:
                rows[qid]["status"] = "running"
            checkpoint()
            image_paths = [
                input_file.parent / image["path"] for item in selected for image in item["images"]
            ]
            try:
                result = await adapter.assess(prompt=prompt, image_paths=image_paths)
                audit = asdict(result)
                checked = validate_assessments(result.raw_json_result, qids, index)
            except AIAdapterError as exc:
                audit = {
                    "provider": args.provider,
                    "model": configured_model,
                    "error_code": getattr(exc, "error_code", type(exc).__name__),
                    "attempts": getattr(exc, "attempts", 1),
                    "usage": getattr(exc, "usage", None),
                    "raw_response": getattr(exc, "raw_response", None),
                    "raw_json_result": getattr(exc, "raw_json_result", None),
                }
                checked = {
                    qid: {
                        "status": "failed",
                        "assessment": None,
                        "required_point_ids": [],
                        "error_code": audit["error_code"],
                    }
                    for qid in qids
                }
            audit["question_ids"] = qids
            write_json(call_dir / "result.json", audit, exclusive=True)
            for qid, update in checked.items():
                rows[qid].update(update, call_id=call_id)
            calls.append(audit)
            checkpoint()
            print(
                json.dumps({"call_id": call_id, **summarize(rows, calls)}, ensure_ascii=False),
                flush=True,
            )

    batches = [
        pending[start : start + args.batch_size]
        for start in range(0, len(pending), args.batch_size)
    ]
    await asyncio.gather(
        *(batch(qids, sequence + number) for number, qids in enumerate(batches, 1))
    )
    checkpoint()
    render_results(output, rows, index, entries, input_file.parent)
    return summarize(rows, calls)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--curriculum", type=Path, required=True)
    parser.add_argument("--provider", choices=["codex", "deepseek"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question-ids-file", type=Path)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=480)
    parser.add_argument("--codex-executable", default=os.environ.get("CODEX_EXECUTABLE", "codex"))
    parser.add_argument("--codex-model", default=os.environ.get("CODEX_MODEL") or None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 10 or not 1 <= args.concurrency <= 4:
        parser.error("batch size must be 1-10 and concurrency 1-4")
    try:
        output = args.output.resolve(strict=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        with (output.parent / f".{output.name}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            summary = asyncio.run(run(args))
    except (ValueError, OSError, AIAdapterError) as exc:
        print(f"Semantic run failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 3 if summary["failed"] or summary["not_attempted"] or summary["running"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
