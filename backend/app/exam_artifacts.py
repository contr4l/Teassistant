"""Read identity-bound exam intake artifacts from private storage."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.exam_contracts import ExamIntake

_LEGACY_INTAKE = re.compile(
    r"^jobs/([a-f0-9]{32})/([A-Za-z0-9_-]{1,128})/intake/intake-with-regions\.json$"
)


class ArtifactUnavailable(ValueError):
    """An exam artifact is absent, unsafe, corrupt, or bound to another exam."""


def _relative_parts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ArtifactUnavailable("exam artifact unavailable")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ArtifactUnavailable("exam artifact unavailable")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise ArtifactUnavailable("exam artifact unavailable")
    return path.parts


def _safe_existing_path(root: Path, relative: Any, *, directory: bool) -> Path:
    parts = _relative_parts(relative)
    try:
        real_root = root.resolve(strict=True)
        unresolved = real_root
        for part in parts:
            unresolved = unresolved / part
            if unresolved.is_symlink():
                raise ArtifactUnavailable("exam artifact unavailable")
        candidate = unresolved.resolve(strict=True)
        if not candidate.is_relative_to(real_root):
            raise ArtifactUnavailable("exam artifact unavailable")
        if directory and not candidate.is_dir():
            raise ArtifactUnavailable("exam artifact unavailable")
        if not directory and not candidate.is_file():
            raise ArtifactUnavailable("exam artifact unavailable")
        return candidate
    except (OSError, RuntimeError, ValueError):
        raise ArtifactUnavailable("exam artifact unavailable") from None


def _load_intake(path: Path, *, exam_id: str, student_id: str, question_count: Any) -> ExamIntake:
    try:
        intake = ExamIntake.model_validate_json(path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError):
        raise ArtifactUnavailable("exam artifact unavailable") from None
    if intake.exam_id != exam_id or intake.student_id != student_id:
        raise ArtifactUnavailable("exam artifact unavailable")
    if type(question_count) is int and len(intake.expected_question_numbers) != question_count:
        raise ArtifactUnavailable("exam artifact unavailable")
    return intake


def _validate_asset_binding(
    root: Path,
    asset_relative: Any,
    *,
    intake: ExamIntake,
    exam_id: str,
    student_id: str,
    question_count: Any,
) -> None:
    _safe_existing_path(root, asset_relative, directory=True)
    original_path = _safe_existing_path(root, f"{asset_relative}/intake.json", directory=False)
    original = _load_intake(
        original_path,
        exam_id=exam_id,
        student_id=student_id,
        question_count=question_count,
    )
    original_materials = [item.model_dump(mode="json") for item in original.materials]
    current_materials = [item.model_dump(mode="json") for item in intake.materials]
    original_pages = {page.id: page.model_dump(mode="json") for page in original.pages}
    current_pages = {page.id: page.model_dump(mode="json") for page in intake.pages}
    if (
        original.expected_question_numbers != intake.expected_question_numbers
        or original_materials != current_materials
        or any(current_pages.get(page_id) != page for page_id, page in original_pages.items())
    ):
        raise ArtifactUnavailable("exam artifact unavailable")


def read_exam_intake(
    settings: Any,
    data: dict[str, Any],
    *,
    exam_id: str,
    student_id: str,
) -> tuple[ExamIntake, str]:
    """Read the referenced intake and return it with its effective private relative path.

    The one legacy migration case is deliberately exact and only applies when the
    pointed-to intake does not exist. All errors use a stable, path-free message.
    """
    root = Path(settings.data_dir)
    intake_relative = data.get("intake_path")
    asset_relative = data.get("asset_root")
    primary_parts = _relative_parts(intake_relative)
    asset_parts = _relative_parts(asset_relative)
    _safe_existing_path(root, asset_relative, directory=True)
    primary = root.joinpath(*primary_parts)

    try:
        primary_exists = os.path.lexists(primary)
    except OSError:
        raise ArtifactUnavailable("exam artifact unavailable") from None
    if primary_exists:
        intake_path = _safe_existing_path(root, intake_relative, directory=False)
        intake = _load_intake(
            intake_path,
            exam_id=exam_id,
            student_id=student_id,
            question_count=data.get("question_count"),
        )
        _validate_asset_binding(
            root,
            asset_relative,
            intake=intake,
            exam_id=exam_id,
            student_id=student_id,
            question_count=data.get("question_count"),
        )
        return intake, "/".join(primary_parts)

    legacy = _LEGACY_INTAKE.fullmatch(intake_relative)
    if legacy is None:
        raise ArtifactUnavailable("exam artifact unavailable")
    expected_asset = ("jobs", legacy.group(1), legacy.group(2), "intake")
    if asset_parts != expected_asset:
        raise ArtifactUnavailable("exam artifact unavailable")

    fallback_relative = "/".join((*expected_asset[:-1], "run", "intake-with-regions.json"))
    fallback = _safe_existing_path(root, fallback_relative, directory=False)
    intake = _load_intake(
        fallback,
        exam_id=exam_id,
        student_id=student_id,
        question_count=data.get("question_count"),
    )
    _validate_asset_binding(
        root,
        asset_relative,
        intake=intake,
        exam_id=exam_id,
        student_id=student_id,
        question_count=data.get("question_count"),
    )
    return intake, fallback_relative
