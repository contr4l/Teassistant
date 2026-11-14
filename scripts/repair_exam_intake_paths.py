"""Diagnose missing analysis artifacts; explicitly repair only verified legacy intake pointers.

No model calls. Run inside the API container; dry-run is the default. On an older
image copy exam_artifacts.py next to this script to load the compatibility helper.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "backend").is_dir():
    sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import exists, select, update  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

try:
    from app.exam_artifacts import ArtifactUnavailable, read_exam_intake
except ModuleNotFoundError as exc:
    if exc.name != "app.exam_artifacts":
        raise
    # Explicit compatibility for a copied maintenance bundle on the old image.
    from exam_artifacts import ArtifactUnavailable, read_exam_intake

from app.exam_contracts import ExamAnalysis  # noqa: E402
from app.exam_intake import ExamIntakeError, _load_and_validate_assets  # noqa: E402
from app.semantic_bank import canonical_hash  # noqa: E402
from app.web_settings import WebSettings  # noqa: E402
from app.web_store import Store, _Job, _now, _Record  # noqa: E402


def _private(root: Path, relative: str, *, directory=False) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("unsafe private locator")
    if PurePosixPath(relative).is_absolute() or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("unsafe private locator")
    base = root.resolve(strict=True)
    current = base
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError("unsafe private locator")
    path = current.resolve(strict=True)
    if not path.is_relative_to(base) or not (path.is_dir() if directory else path.is_file()):
        raise ValueError("unavailable private artifact")
    return path


def _active(resource_id):
    return exists(select(_Job.id).where(
        _Job.resource_id == resource_id,
        _Job.kind.in_(("analysis", "correction")),
        _Job.status.in_(("queued", "running")),
    ))


def _verified_candidate(settings, row):
    data = row["data"]
    analysis_file = _private(settings.data_dir, data["analysis_path"])
    analysis_raw = analysis_file.read_bytes()
    analysis = ExamAnalysis.model_validate_json(analysis_raw)
    if (analysis.exam_id != row["id"] or analysis.student_id != data.get("student_id")
            or analysis.revision != data.get("analysis_revision")):
        raise ValueError("analysis identity mismatch")
    intake, actual = read_exam_intake(
        settings, data, exam_id=row["id"], student_id=data["student_id"],
    )
    if actual == data.get("intake_path"):
        return None
    material_sha = canonical_hash(sorted({(m.role, m.sha256) for m in intake.materials}))
    if (analysis.expected_question_numbers != intake.expected_question_numbers
            or analysis.source_material_sha256 != material_sha):
        raise ValueError("analysis material binding mismatch")
    asset_root = _private(settings.data_dir, data["asset_root"], directory=True)
    _load_and_validate_assets(intake, asset_root)
    # Read again before publishing a pointer; never accept a changing artifact.
    if analysis_file.read_bytes() != analysis_raw:
        raise ValueError("analysis changed during diagnostic")
    return actual


def _audit(settings, row, target):
    root = settings.data_dir.resolve(strict=True)
    folder = root / "maintenance"
    if folder.is_symlink():
        raise ValueError("unsafe audit directory")
    folder.mkdir(mode=0o700, exist_ok=True)
    if not folder.resolve(strict=True).is_relative_to(root):
        raise ValueError("unsafe audit directory")
    path = folder / ("exam-intake-path-" + uuid.uuid4().hex + ".json")
    payload = {
        "schema_version": "exam-intake-path-repair-v1", "created_at": _now().isoformat(),
        "record_id": row["id"], "owner_id": row["owner_id"],
        "expected_revision": row["revision"], "old_intake_path": row["data"]["intake_path"],
        "new_intake_path": target, "state": "planned",
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return path, payload


def _finish_audit(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def diagnose_or_repair(store, settings, *, apply=False, exam_id=None):
    """Return aggregate diagnostics without names, paper content, DSNs or credentials."""
    counters = Counter()
    statement = select(_Record.id, _Record.owner_id).where(_Record.kind == "exam")
    if exam_id is not None:
        statement = statement.where(_Record.id == exam_id)
    with store._sessions() as session:
        identities = list(session.execute(statement))
    for identifier, owner in identities:
        row = store.get_record("exam", identifier, owner)
        if row is None:
            counters["conflict"] += 1
            continue
        if not row["data"].get("analysis_path"):
            counters["not_analyzed"] += 1
            continue
        with store._sessions() as session:
            busy = session.scalar(select(_active(identifier)))
        if busy:
            counters["active_job_skipped"] += 1
            continue
        try:
            _private(settings.data_dir, row["data"]["analysis_path"])
        except (OSError, ValueError):
            counters["analysis_file_unavailable"] += 1
            continue
        if row["data"].get("teacher_report") is None and row["data"].get("teacher_report_path"):
            try:
                _private(settings.data_dir, row["data"]["teacher_report_path"])
            except (OSError, ValueError):
                counters["teacher_report_file_unavailable"] += 1
                continue
        try:
            target = _verified_candidate(settings, row)
        except (OSError, ValueError, KeyError, TypeError, ArtifactUnavailable, ExamIntakeError):
            counters["intake_or_evidence_invalid"] += 1
            continue
        if target is None:
            counters["current_pointer_valid"] += 1
            continue
        counters["legacy_pointer_repairable"] += 1
        if not apply:
            continue
        path, audit = _audit(settings, row, target)
        try:
            # Revalidate just before the guarded update; no stale diagnostic plan.
            if _verified_candidate(settings, row) != target:
                raise ValueError("candidate changed")
            data = copy.deepcopy(row["data"])
            data["intake_path"] = target
            with store._sessions.begin() as session:
                result = session.execute(update(_Record).where(
                    _Record.kind == "exam", _Record.id == identifier,
                    _Record.owner_id == owner, _Record.revision == row["revision"],
                    ~_active(identifier),
                ).values(data=data, revision=row["revision"] + 1, updated_at=_now()))
                changed = result.rowcount == 1
            audit["state"] = "applied" if changed else "conflict"
            counters["repaired" if changed else "conflict"] += 1
        except (OSError, ValueError, KeyError, TypeError, ExamIntakeError):
            audit["state"] = "revalidation_failed"
            counters["revalidation_failed"] += 1
        finally:
            # The original planned audit already exists privately if interrupted.
            _finish_audit(path, audit)
    return {"mode": "apply" if apply else "diagnose", "exam_count": len(identities),
            "counts": dict(counters), "model_calls": 0, "service_restart_required": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="update verified legacy pointers only")
    parser.add_argument("--exam-id", help="optional exact exam record ID; never a student name")
    args = parser.parse_args(argv)
    if args.exam_id is not None and not re.fullmatch(r"[a-f0-9]{32}", args.exam_id):
        parser.error("exam-id must be a 32-character lowercase hex ID")
    if not os.environ.get("TEASSISTANT_DATABASE_URL", "").strip():
        parser.error("run inside the configured API container; explicit database required")
    store = None
    try:
        settings = WebSettings.from_env()
        settings.data_dir.resolve(strict=True)
        url = make_url(settings.database_url)
        if url.get_backend_name() == "sqlite" and (
            not url.database or url.database == ":memory:" or not Path(url.database).is_file()
        ):
            raise ValueError("existing database required")
        store = Store(settings.database_url)
        # Deliberately no store.init(): a diagnostic must not create/migrate tables.
        print(json.dumps(diagnose_or_repair(store, settings, apply=args.apply, exam_id=args.exam_id),
                         ensure_ascii=False))
        return 0
    except Exception:
        print("exam artifact diagnostic failed; verify service database and private storage", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
