"""Fail-closed DOCX import pipeline for administrator question-bank updates."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.bank_practice import load_practice_bank
from app.contracts import Curriculum
from app.curriculum import CurriculumIndex
from app.model_billing import BillingError
from app.model_runtime import BillingContext, MeteredTransport, MeteringError
from app.model_settings import ModelSettingsError, ModelSettingsStore
from app.semantic_bank import build_semantic_prompt, canonical_hash, validate_assessments
from app.semantic_contracts import DIFFICULTY_VERSION, PROMPT_VERSION
from app.semantic_inputs import render_inputs
from app.semantic_models import DeepSeekSemanticAdapter


class AdminBankImportError(RuntimeError):
    """Safe import failure; messages never contain source content or credentials."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _load_script_module(module_name: str):
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise AdminBankImportError("题库处理组件不可用")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _safe_job_files(store, settings, job: dict) -> list[Path]:
    data = job.get("input_data")
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        raise AdminBankImportError("导入文件清单无效")
    root = Path(settings.data_dir).resolve(strict=True)
    resolved: list[Path] = []
    seen: set[tuple[str, str]] = set()
    for item in files:
        if not isinstance(item, dict):
            raise AdminBankImportError("导入文件清单无效")
        relative, expected = item.get("path"), item.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise AdminBankImportError("导入文件清单无效")
        lexical = root / relative
        candidate = lexical.resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError:
            raise AdminBankImportError("导入文件路径无效") from None
        cursor = root
        for part in lexical.relative_to(root).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise AdminBankImportError("导入文件路径无效")
        filename = item.get("filename")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or filename != candidate.name
            or not candidate.is_file()
            or candidate.suffix.lower() != ".docx"
        ):
            raise AdminBankImportError("仅支持有效 DOCX 文件")
        raw = candidate.read_bytes()
        if _sha(raw) != expected:
            raise AdminBankImportError("导入文件校验失败")
        key = (str(item.get("filename", "")), expected)
        if key in seen:
            raise AdminBankImportError("重复上传文件")
        seen.add(key)
        resolved.append(candidate)
    if not resolved:
        raise AdminBankImportError("没有可导入文件")
    return resolved


def _source_root(base_bank_dir: Path) -> Path:
    root = Path(base_bank_dir).resolve(strict=True)
    if (root / "source" / "bank.json").is_file():
        return root / "source"
    if (root / "bank.json").is_file():
        return root
    raise AdminBankImportError("当前题库来源包不可用")


def _merge_sources(existing_root: Path | None, uploaded: list[Path], target: Path) -> int:
    from app.docx_ingest import _pair_title, _role_for_filename

    target.mkdir(parents=True, exist_ok=False)
    current: dict[tuple[str, str], tuple[Path, str]] = {}
    if existing_root is not None:
        source_dir = existing_root / "sources"
        if not source_dir.is_dir():
            raise AdminBankImportError("当前题库缺少来源原件")
        for path in sorted(source_dir.iterdir(), key=lambda p: p.name):
            if path.suffix.lower() != ".docx":
                continue
            if path.is_symlink() or not path.is_file():
                raise AdminBankImportError("当前题库来源文件无效")
            pair_id, _ = _pair_title(path)
            key = pair_id, _role_for_filename(path)
            digest = _sha(path.read_bytes())
            previous = current.get(key)
            if previous is not None:
                if previous[1] != digest:
                    raise AdminBankImportError("当前题库包含歧义试卷角色，无法安全合并")
                continue
            current[key] = (path, digest)

    incoming: dict[tuple[str, str], tuple[Path, str]] = {}
    for path in uploaded:
        pair_id, _ = _pair_title(path)
        key = pair_id, _role_for_filename(path)
        digest = _sha(path.read_bytes())
        previous = incoming.get(key)
        if previous is not None:
            if previous[1] != digest:
                raise AdminBankImportError("上传文件存在歧义试卷角色")
            continue
        incoming[key] = (path, digest)
    # An uploaded original and analysis document for the same paper are expected;
    # a role is replaced only when that normalized paper-role already exists.
    merged = {key: value[0] for key, value in current.items()}
    merged_docs = {key: value[0] for key, value in incoming.items()}
    merged.update(merged_docs)
    pairs: dict[str, set[str]] = {}
    for pair_id, role in merged:
        pairs.setdefault(pair_id, set()).add(role)
    if any(roles != {"original", "analysis"} for roles in pairs.values()):
        raise AdminBankImportError("每套试卷都必须同时提供原卷和解析")
    names: set[str] = set()
    for _, source in sorted(merged.items()):
        name = source.name
        if name in names:
            raise AdminBankImportError("合并后的来源文件名冲突")
        names.add(name)
        shutil.copyfile(source, target / name)
    return len(merged)


class _CapturingMeteredTransport(MeteredTransport):
    def __init__(self, *args, capture_dir: Path, network_factory=None, **kwargs):
        super().__init__(
            *args,
            inner=network_factory() if network_factory is not None else None,
            **kwargs,
        )
        self.capture_dir = capture_dir
        self.synthetic = network_factory is not None
        self.raw_responses: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        self.raw_responses.append(response.content.decode("utf-8", errors="replace"))
        return response


async def _assess_batches(
    *, store, settings, job, curriculum, bank_root, inputs_dir, run_dir,
    transport_factory=None, network_factory=None
):
    data = job["input_data"]
    transport_factory = transport_factory or _CapturingMeteredTransport
    synthetic_execution = network_factory is not None or bool(
        getattr(transport_factory, "synthetic", False)
    )
    if curriculum.is_synthetic and not synthetic_execution:
        raise AdminBankImportError("真实题库导入不能使用合成课程目录")
    context = BillingContext.from_job(job)
    context.check_job(store)
    config, snapshot = ModelSettingsStore(settings.data_dir).runtime(
        timeout=settings.model_timeout_seconds
    )
    bank_raw = (bank_root / "bank.json").read_bytes()
    input_raw = (inputs_dir / "inputs.json").read_bytes()
    curriculum_raw = Path(settings.curriculum_path).read_bytes()
    curriculum_hash = _sha(curriculum_raw)
    if curriculum_hash != data.get("curriculum_sha256"):
        raise AdminBankImportError("课程目录在导入期间已变化")
    inputs = json.loads(input_raw)
    source_bank = json.loads(bank_raw)
    entries = {row["question_id"]: row for row in inputs["questions"]}
    question_ids = list(entries)
    if not question_ids:
        raise AdminBankImportError("新题库没有可处理题目")
    index = CurriculumIndex(curriculum)
    semantic_dir = run_dir
    calls_dir = semantic_dir / "calls"
    calls_dir.mkdir(parents=True)
    rows: dict[str, dict] = {}
    for qid in question_ids:
        question = next(row for row in source_bank["questions"] if row["id"] == qid)
        rows[qid] = {
            "status": "not_attempted",
            "association_status": question.get("association_status"),
            "source_hash": canonical_hash(question),
            "assessment": None,
            "required_point_ids": [],
            "error_code": None,
        }
    metadata = {
        "schema_version": "source-semantic-run-v1",
        "source_bank_sha256": _sha(bank_raw),
        "inputs_sha256": _sha(input_raw),
        "curriculum_sha256": curriculum_hash,
        "validated_curriculum_sha256": curriculum_hash,
        "curriculum_version": curriculum.version,
        "curriculum_status": "engineering_draft",
        "prompt_version": PROMPT_VERSION,
        "difficulty_version": DIFFICULTY_VERSION,
        "provider": "deepseek",
        "configured_model": config.model,
        "codex_executable": None,
        "mode": "synthetic_test" if synthetic_execution else "business_model_trial",
        "synthetic": synthetic_execution,
        "question_ids": question_ids,
        "practice_ready": False,
    }
    _write_json(semantic_dir / "run.json", metadata)
    (semantic_dir / "curriculum.json").write_bytes(curriculum_raw)
    batch_size = 4
    safe_failure = None
    for offset in range(0, len(question_ids), batch_size):
        qids = question_ids[offset : offset + batch_size]
        call_id = offset // batch_size + 1
        call_name = f"{call_id:04d}"
        call_dir = calls_dir / call_name
        call_dir.mkdir()
        selected = [entries[qid] for qid in qids]
        prompt = build_semantic_prompt(selected, index)
        prompt_raw = prompt.encode("utf-8")
        request_raw = _write_json(
            call_dir / "request.json",
            {
                "question_ids": qids,
                "prompt_sha256": _sha(prompt_raw),
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        (call_dir / "prompt.txt").write_bytes(prompt_raw)
        transport = transport_factory(
            store=store,
            context=context,
            stage="admin_bank_semantic",
            snapshot=snapshot,
            capture_dir=call_dir,
            network_factory=network_factory,
        )
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=config.timeout_seconds,
                follow_redirects=False,
            ) as client:
                result = await DeepSeekSemanticAdapter(config, client=client).assess(
                    prompt=prompt,
                    image_paths=[inputs_dir / image["path"] for row in selected for image in row["images"]],
                )
            checked = validate_assessments(result.raw_json_result, qids, index)
            audit = asdict(result)
            audit["raw_response"] = transport.raw_responses[-1] if transport.raw_responses else None
            audit["raw_responses"] = list(transport.raw_responses)
            audit["question_ids"] = qids
            audit["synthetic"] = bool(getattr(transport, "synthetic", synthetic_execution))
            audit["request_sha256"] = _sha(request_raw)
            audit["prompt_sha256"] = _sha(prompt_raw)
        except Exception as exc:
            if isinstance(exc, (BillingError, MeteringError, ModelSettingsError)):
                safe_failure = str(exc)
            # Preserve a failure record without exposing provider payloads, secrets, or prompt data.
            private_raw = getattr(exc, "raw_response", None)
            audit = {
                "provider": "deepseek",
                "model": config.model,
                "error_code": getattr(exc, "error_code", type(exc).__name__),
                "attempts": getattr(exc, "attempts", 1),
                "usage": getattr(exc, "usage", None),
                "raw_response": private_raw or (transport.raw_responses[-1] if transport.raw_responses else None),
                "raw_responses": list(transport.raw_responses),
                "raw_json_result": getattr(exc, "raw_json_result", None),
                "question_ids": qids,
                "synthetic": bool(getattr(transport, "synthetic", synthetic_execution)),
            }
            checked = {
                qid: {"status": "failed", "assessment": None, "required_point_ids": [], "error_code": audit["error_code"]}
                for qid in qids
            }
        result_raw = _write_json(call_dir / "result.json", audit)
        request_hash, prompt_hash, result_hash = _sha(request_raw), _sha(prompt_raw), _sha(result_raw)
        for qid, update in checked.items():
            # A schema-valid insufficient_evidence assessment is a completed model
            # result. The loader keeps it visible and blocks it with its warning.
            if update.get("error_code") == "insufficient_evidence" and update.get("assessment"):
                update = {**update, "status": "assessed", "error_code": None}
            if update["status"] == "failed" or update.get("assessment") is None:
                rows[qid].update(update, call_id=call_id)
                continue
            rows[qid].update(
                update,
                call_id=call_id,
                validated_catalog_version=curriculum.version,
                validated_catalog_sha256=curriculum_hash,
                provenance={
                    "source_run_path": "semantic-run",
                    "call_id": call_id,
                    "call_directory": f"calls/{call_name}",
                    "request_sha256": request_hash,
                    "prompt_sha256": prompt_hash,
                    "result_sha256": result_hash,
                    "response_item_sha256": canonical_hash(update["assessment"]),
                },
            )
    failed = [qid for qid, row in rows.items() if row["status"] != "assessed"]
    _write_json(semantic_dir / "annotations.json", {"run": metadata, "questions": rows})
    _write_json(
        semantic_dir / "summary.json",
        {
            "denominator": len(rows),
            "assessed": len(rows) - len(failed),
            "failed": len(failed),
            "synthetic": synthetic_execution,
        },
    )
    if failed:
        raise AdminBankImportError(safe_failure or "模型未能为全部新题库题目生成有效结构化结果")
    return metadata


def process_import(store, settings, job, directory, *, base_bank_dir=None) -> dict:
    """Build, reassess, validate, and package a complete immutable bank candidate."""
    directory = Path(directory).resolve(strict=True)
    package_root = directory / "package"
    try:
        if not isinstance(job, dict) or job.get("kind") != "bank_import":
            raise AdminBankImportError("任务类型无效")
        if package_root.exists() or package_root.is_symlink():
            raise AdminBankImportError("任务候选目录已存在")
        files = _safe_job_files(store, settings, job)
        source_root = _source_root(Path(base_bank_dir)) if base_bank_dir is not None else None
        merged_sources = directory / "merged-sources"
        source_count = _merge_sources(source_root, files, merged_sources)

        build_script = _load_script_module("build_question_bank")
        source_build = directory / "source-build"
        summary = build_script.build(merged_sources, source_build)
        if summary.get("failed_files"):
            raise AdminBankImportError("DOCX 来源解析失败")
        bank_data = json.loads((source_build / "bank.json").read_bytes())
        if not bank_data.get("questions"):
            raise AdminBankImportError("新题库没有可用题目")
        chromium = os.getenv("TEASSISTANT_CHROMIUM") or shutil.which("chromium")
        inputs_dir = directory / "rendered-inputs"
        render_inputs(
            source_build,
            inputs_dir,
            [question["id"] for question in bank_data["questions"]],
            chromium_executable=chromium,
        )
        curriculum = Curriculum.model_validate_json(Path(settings.curriculum_path).read_bytes())
        semantic_dir = directory / "semantic-run"
        asyncio.run(
            _assess_batches(
                store=store,
                settings=settings,
                job=job,
                curriculum=curriculum,
                bank_root=source_build,
                inputs_dir=inputs_dir,
                run_dir=semantic_dir,
            )
        )
        package_parent = directory / "package-stage"
        package_parent.mkdir()
        package_script = _load_script_module("package_question_bank")
        built_package = package_parent / "candidate"
        package_script.package_question_bank(source_build, semantic_dir, built_package)
        practice = load_practice_bank(built_package / "source", built_package / "semantic")
        if not any(item.status == "eligible" for item in practice.questions):
            raise AdminBankImportError("新题库没有可用题目")
        relative_package_path = package_root.relative_to(Path(settings.data_dir).resolve()).as_posix()
        os.rename(built_package, package_root)
        return {
            "package_path": relative_package_path,
            "question_count": len(practice.questions),
            "eligible_count": sum(item.status == "eligible" for item in practice.questions),
            "blocked_count": sum(item.status == "blocked" for item in practice.questions),
            "source_count": source_count,
            "semantic_assessed_count": len(practice.questions),
        }
    except AdminBankImportError:
        raise
    except (BillingError, MeteringError, ModelSettingsError) as exc:
        raise AdminBankImportError(str(exc)) from None
    except Exception as exc:
        raise AdminBankImportError("题库导入失败，当前版本未变更") from exc
