"""Private, source-preserving intake for student exams and source references."""

from __future__ import annotations

import hashlib
import io
import math
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.exam_contracts import ExamIntake, ExamMaterial, ExamPage, ExamRegion

ExamRole = Literal["paper", "response", "reference"]


class ExamIntakeError(RuntimeError):
    """Safe intake failure that never includes an input path or image contents."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _page_id(role: str, document_sha: str, source_page: int) -> str:
    seed = f"exam-page-v1\0{role}\0{document_sha}\0{source_page}".encode("ascii")
    return f"page-{hashlib.sha256(seed).hexdigest()[:24]}"


def _resolve_input(path: Path) -> tuple[Path, bytes, str, str]:
    try:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file():
            raise OSError
        content = resolved.read_bytes()
    except (OSError, RuntimeError, TypeError):
        raise ExamIntakeError("source_input_unavailable") from None
    if content.startswith(b"%PDF-"):
        return resolved, content, _sha256(content), "pdf"
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = (image.format or "").upper()
            if image_format not in {"JPEG", "PNG"}:
                raise ExamIntakeError("source_image_format_unsupported")
            image.verify()
    except ExamIntakeError:
        raise
    except Exception:
        raise ExamIntakeError("source_input_format_unsupported") from None
    return resolved, content, _sha256(content), "jpg" if image_format == "JPEG" else "png"


def _safe_output_path(output_dir: Path, sources: list[Path]) -> tuple[Path, Path]:
    try:
        output = Path(output_dir).expanduser().resolve(strict=False)
        if output.exists() or output.is_symlink():
            raise ExamIntakeError("output_already_exists")
        if output.name in {"", ".", ".."}:
            raise ExamIntakeError("output_path_invalid")
        for source in sources:
            if (
                output == source
                or output in source.parents
                or source.parent == output
                or source.parent in output.parents
            ):
                raise ExamIntakeError("output_must_be_separate_from_sources")
        return output, output.parent
    except ExamIntakeError:
        raise
    except (OSError, RuntimeError, TypeError):
        raise ExamIntakeError("output_path_invalid") from None


def _save_page(
    image: Image.Image, output: Path, *, role: str, document_sha: str, page_number: int
) -> ExamPage:
    page_id = _page_id(role, document_sha, page_number)
    path = output / "pages" / f"{page_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_rgb(image)
    normalized.save(path, format="PNG", optimize=True)
    data = path.read_bytes()
    return ExamPage(
        id=page_id,
        document_sha256=document_sha,
        source_page=page_number,
        role=role,
        image_path=path.relative_to(output).as_posix(),
        image_sha256=_sha256(data),
        width=normalized.width,
        height=normalized.height,
    )


def _normalize_rgb(image: Image.Image) -> Image.Image:
    oriented = ImageOps.exif_transpose(image)
    if oriented.mode == "RGB":
        return oriented.copy()
    if oriented.mode == "RGBA":
        background = Image.new("RGB", oriented.size, "white")
        background.paste(oriented, mask=oriented.getchannel("A"))
        return background
    return oriented.convert("RGB")


def _render_image_file(source: Path, output: Path, role: str, document_sha: str) -> list[ExamPage]:
    pages: list[ExamPage] = []
    try:
        with Image.open(source) as image:
            frame_count = getattr(image, "n_frames", 1)
            for index in range(frame_count):
                image.seek(index)
                frame = image.copy()
                pages.append(
                    _save_page(
                        frame,
                        output,
                        role=role,
                        document_sha=document_sha,
                        page_number=index + 1,
                    )
                )
    except ExamIntakeError:
        raise
    except Exception:
        raise ExamIntakeError("source_image_decode_failed") from None
    if not pages:
        raise ExamIntakeError("source_image_has_no_pages")
    return pages


def _render_pdf(
    source: Path, output: Path, role: str, document_sha: str, dpi: int
) -> list[ExamPage]:
    pages: list[ExamPage] = []
    document = None
    try:
        document = pdfium.PdfDocument(str(source))
        if len(document) < 1:
            raise ExamIntakeError("source_pdf_has_no_pages")
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=dpi / 72.0, rotation=0)
                image = bitmap.to_pil()
                pages.append(
                    _save_page(
                        image,
                        output,
                        role=role,
                        document_sha=document_sha,
                        page_number=index + 1,
                    )
                )
                bitmap.close()
            finally:
                page.close()
    except ExamIntakeError:
        raise
    except Exception:
        raise ExamIntakeError("source_pdf_render_failed") from None
    finally:
        if document is not None:
            document.close()
    return pages


def prepare_exam_intake(
    *,
    exam_id: str,
    student_id: str,
    expected_question_numbers: list[int],
    paper_paths: list[Path],
    response_paths: list[Path],
    reference_paths: list[Path],
    output_dir: Path,
    is_synthetic_student: bool = False,
    dpi: int = 300,
) -> ExamIntake:
    """Normalize inputs and atomically create an immutable, hashed intake bundle."""
    if not isinstance(exam_id, str) or not exam_id.strip():
        raise ExamIntakeError("exam_id_required")
    if not isinstance(student_id, str) or not student_id.strip():
        raise ExamIntakeError("student_id_required")
    if type(is_synthetic_student) is not bool:
        raise ExamIntakeError("synthetic_flag_invalid")
    if type(dpi) is not int or not 72 <= dpi <= 600:
        raise ExamIntakeError("dpi_out_of_range")
    if not expected_question_numbers or any(
        type(number) is not int or number < 1 for number in expected_question_numbers
    ):
        raise ExamIntakeError("expected_question_numbers_invalid")
    if len(expected_question_numbers) != len(set(expected_question_numbers)):
        raise ExamIntakeError("expected_question_numbers_duplicate")

    raw_inputs: list[tuple[ExamRole, Path]] = [
        *(("paper", Path(path)) for path in paper_paths),
        *(("response", Path(path)) for path in response_paths),
        *(("reference", Path(path)) for path in reference_paths),
    ]
    if not raw_inputs:
        raise ExamIntakeError("at_least_one_source_required")
    resolved: list[tuple[ExamRole, Path, bytes, str, str]] = []
    seen_input_paths: set[tuple[str, str]] = set()
    for role, path in raw_inputs:
        source, content, digest, kind = _resolve_input(path)
        key = (role, str(source))
        if key in seen_input_paths:
            continue
        seen_input_paths.add(key)
        resolved.append((role, source, content, digest, kind))
    output, parent = _safe_output_path(Path(output_dir), [entry[1] for entry in resolved])
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=parent))
    except OSError:
        raise ExamIntakeError("output_parent_unavailable") from None

    try:
        sources_dir = staging / "sources"
        sources_dir.mkdir()
        materials: list[ExamMaterial] = []
        material_keys: set[tuple[str, str]] = set()
        pages: list[ExamPage] = []
        rendered_by_source: dict[tuple[str, str], tuple[str, list[ExamPage]]] = {}
        for role, _original_path, content, digest, kind in resolved:
            key = (role, digest)
            if key not in rendered_by_source:
                role_dir = sources_dir / role
                role_dir.mkdir(exist_ok=True)
                copied_source = role_dir / f"{digest}.{kind}"
                copied_source.write_bytes(content)
                if kind == "pdf":
                    physical_pages = _render_pdf(copied_source, staging, role, digest, dpi)
                else:
                    physical_pages = _render_image_file(copied_source, staging, role, digest)
                rendered_by_source[key] = (
                    copied_source.relative_to(staging).as_posix(),
                    physical_pages,
                )
                pages.extend(physical_pages)
            source_copy, physical_pages = rendered_by_source[key]
            if key not in material_keys:
                materials.append(
                    ExamMaterial(
                        id=f"material-{role}-{digest[:24]}",
                        sha256=digest,
                        source_path=source_copy,
                        role=role,
                        page_ids=[page.id for page in physical_pages],
                    )
                )
                material_keys.add(key)

        intake = ExamIntake(
            exam_id=exam_id,
            student_id=student_id,
            is_synthetic_student=is_synthetic_student,
            expected_question_numbers=expected_question_numbers,
            materials=materials,
            pages=pages,
        )
        (staging / "intake.json").write_text(
            intake.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        if output.exists() or output.is_symlink():
            raise ExamIntakeError("output_already_exists")
        os.replace(staging, output)
        return intake
    except ExamIntakeError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise ExamIntakeError("intake_build_failed") from None


def _safe_asset_path(root: Path, relative: str) -> Path:
    item = PurePosixPath(relative)
    if item.is_absolute() or any(part in {"", ".", ".."} for part in item.parts):
        raise ExamIntakeError("intake_asset_path_invalid")
    resolved_root = root.resolve(strict=True)
    path = (resolved_root / Path(*item.parts)).resolve(strict=True)
    if resolved_root not in path.parents or not path.is_file():
        raise ExamIntakeError("intake_asset_outside_root")
    return path


def _load_and_validate_assets(intake: ExamIntake, root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    by_id = {page.id: page for page in intake.pages}
    for material in intake.materials:
        source = _safe_asset_path(root, material.source_path)
        try:
            if _sha256(source.read_bytes()) != material.sha256:
                raise ExamIntakeError("intake_source_hash_mismatch")
        except ExamIntakeError:
            raise
        except Exception:
            raise ExamIntakeError("intake_source_unavailable") from None
    for page in intake.pages:
        path = _safe_asset_path(root, page.image_path)
        try:
            data = path.read_bytes()
            if _sha256(data) != page.image_sha256:
                raise ExamIntakeError("intake_page_hash_mismatch")
            with Image.open(path) as image:
                if image.format != "PNG" or image.size != (page.width, page.height):
                    raise ExamIntakeError("intake_page_geometry_mismatch")
                image.verify()
        except ExamIntakeError:
            raise
        except Exception:
            raise ExamIntakeError("intake_page_invalid") from None
        paths[page.id] = path
    for page in intake.pages:
        if page.parent_page_id is not None and page.parent_page_id not in by_id:
            raise ExamIntakeError("intake_crop_parent_missing")
    return paths


def _normalized_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(float(value) for value in bbox)  # type: ignore[return-value]


def crop_exam_regions(
    intake: ExamIntake,
    regions: list[ExamRegion],
    root: Path,
) -> ExamIntake:
    """Append model-selected crops while preserving each canonical source-page transform."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ExamIntakeError("intake_root_invalid")
    page_paths = _load_and_validate_assets(intake, root)
    intake_file = root / "intake.json"
    try:
        if intake_file.is_symlink():
            raise ExamIntakeError("intake_manifest_symlink")
        on_disk = ExamIntake.model_validate_json(intake_file.read_bytes())
    except ExamIntakeError:
        raise
    except Exception:
        raise ExamIntakeError("intake_manifest_invalid") from None
    if on_disk.model_dump(mode="json") != intake.model_dump(mode="json"):
        raise ExamIntakeError("intake_manifest_changed")
    if any(page.parent_page_id is not None for page in intake.pages):
        raise ExamIntakeError("crop_requires_physical_base_intake")

    by_id = {page.id: page for page in intake.pages}
    existing = {page.id: page for page in intake.pages}
    new_pages: list[ExamPage] = []
    staged: dict[str, tuple[Path, bytes]] = {}
    temp_dir = Path(tempfile.mkdtemp(prefix=".exam-crops-", dir=root))
    created_paths: list[Path] = []
    committed = False
    try:
        for raw_region in regions:
            region = (
                raw_region
                if isinstance(raw_region, ExamRegion)
                else ExamRegion.model_validate(raw_region)
            )
            parent = by_id.get(region.page_id)
            if parent is None:
                raise ExamIntakeError("exam_region_page_missing")
            if parent.parent_page_id is not None:
                raise ExamIntakeError("crop_parent_must_be_physical_page")
            if region.role != parent.role:
                raise ExamIntakeError("crop_role_mismatch")
            if any(
                number not in intake.expected_question_numbers for number in region.question_numbers
            ):
                raise ExamIntakeError("crop_question_number_out_of_range")
            bbox = _normalized_bbox(region.bbox)
            with Image.open(page_paths[parent.id]) as opened_parent:
                parent_image = opened_parent.convert("RGB")
            width, height = parent_image.size
            left = max(0, min(width, math.floor(bbox[0] * width)))
            top = max(0, min(height, math.floor(bbox[1] * height)))
            right = max(0, min(width, math.ceil(bbox[2] * width)))
            bottom = max(0, min(height, math.ceil(bbox[3] * height)))
            if left >= right or top >= bottom:
                raise ExamIntakeError("exam_region_empty_crop")
            actual_bbox = (left / width, top / height, right / width, bottom / height)
            crop_identity = (
                f"exam-crop-v1\0{parent.id}\0{','.join(format(v, '.12g') for v in actual_bbox)}"
            )
            crop_id = f"crop-{hashlib.sha256(crop_identity.encode('ascii')).hexdigest()[:24]}"
            if crop_id in existing:
                crop = existing[crop_id]
                if crop.parent_page_id != parent.id or crop.parent_bbox != actual_bbox:
                    raise ExamIntakeError("crop_identity_conflict")
                continue
            crop_image = parent_image.crop((left, top, right, bottom))
            staged_path = temp_dir / f"{crop_id}.png"
            crop_image.save(staged_path, format="PNG", optimize=True)
            image_bytes = staged_path.read_bytes()
            crop_page = ExamPage(
                id=crop_id,
                document_sha256=parent.document_sha256,
                source_page=parent.source_page,
                role=parent.role,
                image_path=f"pages/crops/{crop_id}.png",
                image_sha256=_sha256(image_bytes),
                width=crop_image.width,
                height=crop_image.height,
                parent_page_id=parent.id,
                parent_bbox=actual_bbox,
            )
            new_pages.append(crop_page)
            staged[crop_page.image_path] = (staged_path, image_bytes)
            existing[crop_id] = crop_page

        if not new_pages:
            return intake
        updated = ExamIntake.model_validate(
            {
                **intake.model_dump(mode="json"),
                "pages": [
                    *intake.model_dump(mode="json")["pages"],
                    *(page.model_dump(mode="json") for page in new_pages),
                ],
            }
        )
        pages_root = root / "pages"
        for directory in (pages_root, pages_root / "crops"):
            if directory.is_symlink():
                raise ExamIntakeError("crop_output_symlink_forbidden")
            if directory.exists() and not directory.is_dir():
                raise ExamIntakeError("crop_output_parent_invalid")
            directory.mkdir(exist_ok=True)
            if root not in directory.resolve(strict=True).parents:
                raise ExamIntakeError("crop_output_outside_root")
        for relative, (staged_path, image_bytes) in staged.items():
            destination = root / relative
            if destination.is_symlink():
                raise ExamIntakeError("crop_output_symlink_forbidden")
            if destination.exists():
                destination = _safe_asset_path(root, relative)
                if _sha256(destination.read_bytes()) != _sha256(image_bytes):
                    raise ExamIntakeError("crop_output_exists_with_different_content")
                continue
            try:
                os.link(staged_path, destination)
                created_paths.append(destination)
            except FileExistsError:
                if _sha256(destination.read_bytes()) != _sha256(image_bytes):
                    raise ExamIntakeError("crop_output_exists_with_different_content") from None

        manifest_target = root / "intake-with-regions.json"
        if manifest_target.is_symlink():
            raise ExamIntakeError("derived_manifest_symlink_forbidden")
        if manifest_target.exists():
            try:
                previous = ExamIntake.model_validate_json(manifest_target.read_bytes())
            except Exception:
                raise ExamIntakeError("derived_manifest_invalid") from None
            if previous.model_dump(mode="json") != updated.model_dump(mode="json"):
                raise ExamIntakeError("derived_manifest_already_exists")
            _load_and_validate_assets(previous, root)
            committed = True
            return previous
        manifest_stage = temp_dir / "intake-with-regions.json"
        manifest_stage.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
        try:
            os.link(manifest_stage, manifest_target)
        except OSError:
            raise ExamIntakeError("intake_manifest_update_failed") from None
        committed = True
        return updated
    except ExamIntakeError:
        if not committed:
            for path in created_paths:
                path.unlink(missing_ok=True)
        raise
    except Exception:
        if not committed:
            for path in created_paths:
                path.unlink(missing_ok=True)
        raise ExamIntakeError("exam_crop_failed") from None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
