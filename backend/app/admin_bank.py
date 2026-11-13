"""Versioned administrator metadata, immutable sources, and shared bank resolution."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from lxml import html

from app.bank_practice import load_practice_bank
from app.bank_practice_contracts import AdminBankAdjustment, BankQuestion
from app.bank_view import _safe_fragment
from app.contracts import Curriculum
from app.curriculum import CurriculumIndex
from app.semantic_bank import canonical_hash
from app.web_store import StoreConflict

STATE_ID = "b" * 32
STATE_OWNER = "0" * 32


class BankError(ValueError):
    pass


def state(store):
    row = store.get_record("bank_state", STATE_ID, STATE_OWNER)
    if row is None:
        try:
            row = store.create_record(
                "bank_state", STATE_OWNER,
                {"version": "r1", "package_path": None, "adjustments": {}, "history": []},
                record_id=STATE_ID,
            )
        except StoreConflict:
            row = store.get_record("bank_state", STATE_ID, STATE_OWNER)
    return row


def private_directory(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise BankError("题库目录无效")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(p in {"..", "."} for p in pure.parts):
        raise BankError("题库目录无效")
    current = root.resolve()
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            raise BankError("题库目录不允许符号链接")
    resolved = current.resolve(strict=True)
    if not resolved.is_dir() or not resolved.is_relative_to(root.resolve()):
        raise BankError("题库目录无效")
    return resolved


def effective_settings(store, settings, *, snapshot=None):
    data = (snapshot or state(store))["data"]
    if data.get("package_path") is None:
        return settings
    root = private_directory(settings.data_dir, data["package_path"])
    return replace(settings, bank_dir=root / "source", semantic_dir=root / "semantic")


def load_bank(store, settings, *, snapshot=None):
    snapshot = snapshot or state(store)
    active = effective_settings(store, settings, snapshot=snapshot)
    if active.bank_dir is None or active.semantic_dir is None:
        raise BankError("尚未配置题库，请上传Word原卷与解析")
    try:
        bank = load_practice_bank(active.bank_dir, active.semantic_dir)
        curriculum = Curriculum.model_validate_json(settings.curriculum_path.read_bytes())
        if bank.curriculum_snapshot != curriculum:
            raise BankError("题库与当前教学考点目录不一致")
        index = CurriculumIndex(curriculum)
        adjusted = []
        overrides = snapshot["data"].get("adjustments", {})
        for question in bank.questions:
            value = overrides.get(question.id)
            if value is None:
                adjusted.append(question)
                continue
            adjustment = AdminBankAdjustment.model_validate(value)
            if adjustment.base_revision != question.revision:
                raise BankError("管理员调整与来源版本不一致，请重新检查题库")
            index.validate_ids(adjustment.point_ids + adjustment.prerequisite_ids)
            raw = question.model_dump(mode="json")
            raw.update(
                admin_adjustment=adjustment.model_dump(mode="json"),
                point_ids=adjustment.point_ids,
                prerequisite_ids=adjustment.prerequisite_ids,
                difficulty_score=adjustment.difficulty_score,
                revision=canonical_hash({
                    "base_revision": question.revision,
                    "adjustment": adjustment.model_dump(mode="json"),
                }),
            )
            if not adjustment.enabled:
                raw.update(status="blocked", block_reasons=sorted(set(
                    [*question.block_reasons, "admin_disabled"]
                )))
            adjusted.append(BankQuestion.model_validate(raw))
        raw_bank = bank.model_dump(mode="json")
        raw_bank["questions"] = [q.model_dump(mode="json") for q in adjusted]
        if overrides:
            raw_bank["annotations_sha256"] = canonical_hash({
                "base": bank.annotations_sha256, "adjustments": overrides,
            })
        return type(bank).model_validate(raw_bank)
    except BankError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise BankError("题库完整性检查未通过，请检查来源资料、模型证据及考点目录") from None


def summary(store, settings, *, requester_id=None):
    row = state(store)
    active = effective_settings(store, settings, snapshot=row)
    configured = active.bank_dir is not None and active.semantic_dir is not None
    bank = load_bank(store, settings, snapshot=row) if configured else None
    questions = bank.questions if bank else []
    reasons = Counter(reason for q in questions for reason in q.block_reasons)
    imports = []
    if requester_id:
        for record in reversed(store.list_records("bank_import", requester_id)):
            jobs = store.list_jobs(requester_id, resource_id=record["id"])
            latest = jobs[-1] if jobs else None
            imports.append({
                "id": record["id"], "created_at": record["created_at"],
                "reason": record["data"].get("reason", ""),
                "filenames": [f["filename"] for f in record["data"].get("files", [])],
                "job": {k: latest.get(k) for k in (
                    "id", "kind", "resource_id", "status", "error", "result", "attempts"
                )} if latest else None,
            })
    curriculum = Curriculum.model_validate_json(settings.curriculum_path.read_bytes())
    return {
        "revision": row["revision"], "configured": configured,
        "question_count": len(questions),
        "eligible_count": sum(q.status == "eligible" for q in questions),
        "blocked_count": sum(q.status == "blocked" for q in questions),
        "disabled_count": sum(bool(q.admin_adjustment and not q.admin_adjustment.enabled)
                              for q in questions),
        "source_count": len({d.id for q in questions for d in q.documents}),
        "points": [{"id": p.id, "name": p.name} for p in curriculum.points],
        "active_version": row["data"]["version"],
        "history": [{k: item.get(k) for k in (
            "version", "created_at", "created_by", "reason", "question_count"
        )} for item in reversed(row["data"].get("history", []))],
        "imports": imports[:30], "block_reason_counts": dict(reasons),
    }


def question_summary(question, titles):
    return {
        "id": question.id, "number": question.source_payload.get("number"),
        "title": titles.get(question.source_payload.get("pair_id"), "未命名试卷"),
        "status": question.status, "block_reasons": question.block_reasons,
        "point_ids": question.point_ids, "prerequisite_ids": question.prerequisite_ids,
        "difficulty_score": question.difficulty_score,
        "adjusted": question.admin_adjustment is not None,
        "disabled": bool(question.admin_adjustment and not question.admin_adjustment.enabled),
    }


def source_titles(active):
    raw = json.loads((active.bank_dir / "bank.json").read_bytes())
    return {p["id"]: p.get("title", "") for p in raw.get("pairs", [])}


def list_questions(store, settings, *, q="", status="all", point_id="", offset=0, limit=25):
    row = state(store)
    active = effective_settings(store, settings, snapshot=row)
    if active.bank_dir is None or active.semantic_dir is None:
        return {"revision": row["revision"], "total": 0, "items": []}
    bank = load_bank(store, settings, snapshot=row)
    titles = source_titles(active)
    items = []
    for question in bank.questions:
        item = question_summary(question, titles)
        if status == "disabled" and not item["disabled"]:
            continue
        if status in {"eligible", "blocked"} and question.status != status:
            continue
        if point_id and point_id not in question.point_ids:
            continue
        text = " ".join(str(b.get("text", ""))
                        for b in question.source_payload.get("stem_blocks", []))
        if q.casefold() not in f"{item['id']} {item['title']} {text}".casefold():
            continue
        items.append(item)
    return {"revision": row["revision"], "total": len(items),
            "items": items[offset:offset + limit]}


def _preview_blocks(question, fields, active):
    fragments = []
    for field in fields:
        for block in question.source_payload.get(field, []):
            fragment = _safe_fragment(block.get("html", ""))
            wrapper = html.fragment_fromstring(fragment or "<p></p>", create_parent="div")
            for image in wrapper.xpath(".//img"):
                relative = image.get("src", "")
                pure = PurePosixPath(relative)
                if (pure.is_absolute() or ".." in pure.parts
                        or "\\" in relative or not relative.startswith("media/")):
                    raise BankError("题目预览图片路径无效")
                path = active.bank_dir / relative
                if path.is_symlink() or not path.resolve().is_relative_to(active.bank_dir.resolve()):
                    raise BankError("题目预览图片路径无效")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != question.asset_hashes.get(relative):
                    raise BankError("题目预览图片摘要不一致")
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    raise BankError("题目预览图片格式不支持")
                mime = {".jpg": "jpeg", ".jpeg": "jpeg"}.get(path.suffix.lower(),
                                                              path.suffix.lower()[1:])
                image.set("src", f"data:image/{mime};base64," + base64.b64encode(raw).decode())
            fragments.append(html.tostring(wrapper, encoding="unicode"))
    return "".join(fragments)


def detail(store, settings, question_id):
    row = state(store)
    active = effective_settings(store, settings, snapshot=row)
    bank = load_bank(store, settings, snapshot=row)
    question = next((q for q in bank.questions if q.id == question_id), None)
    if question is None:
        raise KeyError(question_id)
    result = question_summary(question, source_titles(active))
    preview_errors = []

    def preview(fields):
        try:
            return _preview_blocks(question, fields, active)
        except (OSError, ValueError, KeyError, TypeError):
            preview_errors.extend(fields)
            return "<p>此部分预览不可用：来源图片或内容校验失败，请更新Word原件。</p>"

    result.update(
        revision=row["revision"],
        question_html=preview(["stem_blocks"]),
        answer_html=preview(["answer_blocks"]),
        analysis_html=preview(["analysis_blocks"]),
        preview_errors=preview_errors,
        ai_assessment=question.semantic_payload.get("assessment"),
        adjustment=question.admin_adjustment.model_dump(mode="json")
        if question.admin_adjustment else None,
        history=[{
            "version": h["version"], "created_at": h.get("created_at"),
            "created_by": h.get("created_by"), "reason": h.get("reason"),
            "adjustment": h.get("adjustments", {}).get(question_id),
        } for h in reversed(row["data"].get("history", []))],
    )
    return result


def change_data(row, actor, reason, *, question_count=None):
    data = copy.deepcopy(row["data"])
    previous = {k: copy.deepcopy(v) for k, v in data.items() if k != "history"}
    previous.update(created_at=row["updated_at"], created_by=data.get("created_by", "system"),
                    reason=data.get("reason", "初始题库"),
                    question_count=question_count if question_count is not None
                    else data.get("question_count"))
    data["history"].append(previous)
    data.update(version=f"r{row['revision'] + 1}", created_by=actor, reason=reason)
    return data


def save_adjustment(store, settings, question_id, payload, actor):
    row = state(store)
    if payload["expected_revision"] != row["revision"]:
        raise StoreConflict("题库已更新，请刷新后重试")
    bank = load_bank(store, settings, snapshot=row)
    question = next((q for q in bank.questions if q.id == question_id), None)
    if question is None:
        raise KeyError(question_id)
    index = CurriculumIndex(bank.curriculum_snapshot)
    index.validate_ids(payload["point_ids"] + payload["prerequisite_ids"])
    if (not payload["point_ids"] or len(set(payload["point_ids"])) != len(payload["point_ids"])
            or len(set(payload["prerequisite_ids"])) != len(payload["prerequisite_ids"])
            or set(payload["point_ids"]) & set(payload["prerequisite_ids"])):
        raise BankError("目标与前置考点须非重复且不重叠，至少选择一个目标考点")
    base_revision = canonical_hash({
        "source_hash": question.source_hash, "semantic_hash": question.semantic_hash,
    })
    adjustment = AdminBankAdjustment(
        base_revision=base_revision, actor_id=actor,
        created_at=datetime.now(UTC).isoformat(), reason=payload["reason"],
        note=payload.get("note", ""), enabled=payload["enabled"],
        point_ids=payload["point_ids"], prerequisite_ids=payload["prerequisite_ids"],
        difficulty_score=payload["difficulty_score"],
    )
    data = change_data(row, actor, payload["reason"], question_count=len(bank.questions))
    data["adjustments"][question_id] = adjustment.model_dump(mode="json")
    candidate = {**row, "data": data}
    load_bank(store, settings, snapshot=candidate)
    store.update_record("bank_state", STATE_ID, STATE_OWNER, data, row["revision"])
    return detail(store, settings, question_id)


def prepare_import_publication(store, settings, package_path, revision, actor, reason):
    row = state(store)
    if row["revision"] != revision:
        raise StoreConflict("导入期间题库已更新，新版本未发布")
    active = effective_settings(store, settings, snapshot=row)
    previous_count = len(load_bank(store, settings, snapshot=row).questions) if (
        active.bank_dir is not None and active.semantic_dir is not None
    ) else 0
    data = change_data(row, actor, reason, question_count=previous_count)
    data.update(package_path=package_path, adjustments={})
    bank = load_bank(store, settings, snapshot={**row, "data": data})
    if not any(q.status == "eligible" for q in bank.questions):
        raise BankError("新题库无可用题目，当前版本保持不变")
    data["question_count"] = len(bank.questions)
    # Carry explicit administrator decisions only when both source and AI version match.
    # A fresh AI pass changes the evidence identity; no adjustment is silently reused.
    return {"kind": "bank_state", "record_id": STATE_ID, "owner_id": STATE_OWNER,
            "expected_revision": row["revision"], "data": data}


def restore(store, settings, version, revision, actor, reason):
    row = state(store)
    if row["revision"] != revision:
        raise StoreConflict("题库已更新，请刷新后重试")
    previous = next((h for h in row["data"].get("history", []) if h["version"] == version), None)
    if previous is None:
        raise BankError("题库历史版本不存在")
    data = change_data(row, actor, reason)
    data.update(package_path=previous["package_path"],
                adjustments=copy.deepcopy(previous.get("adjustments", {})))
    load_bank(store, settings, snapshot={**row, "data": data})
    store.update_record("bank_state", STATE_ID, STATE_OWNER, data, row["revision"])
    return summary(store, settings, requester_id=actor)
