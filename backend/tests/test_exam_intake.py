from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.exam_contracts import ExamIntake, ExamRegion
from app.exam_intake import ExamIntakeError, crop_exam_regions, prepare_exam_intake
from PIL import Image


def _write_jpeg(
    path: Path, *, size=(80, 40), orientation: int | None = None, color="white"
) -> bytes:
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    image.save(path, format="JPEG", exif=exif)
    return path.read_bytes()


def _vector_two_page_pdf() -> bytes:
    drawing = b"1 0 0 RG 5 w 30 40 m 170 40 l S\n0 0 0 rg BT /F1 12 Tf 20 120 Td (Q1) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(drawing)} >>\nstream\n".encode() + drawing + b"endstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 7 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode())
        result.extend(body)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def _intake(tmp_path: Path, image_path: Path, output: Path):
    return prepare_exam_intake(
        exam_id="exam-demo",
        student_id="student-demo",
        expected_question_numbers=[1, 2, 3],
        paper_paths=[image_path],
        response_paths=[],
        reference_paths=[],
        output_dir=output,
        is_synthetic_student=True,
    )


def test_prepare_intake_deduplicates_same_role_content_and_preserves_original(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    first = input_dir / "response_named_paper.jpg"
    second = input_dir / "duplicate.jpg"
    source = _write_jpeg(first)
    second.write_bytes(source)
    output = tmp_path / "intake"

    intake = prepare_exam_intake(
        exam_id="exam-1",
        student_id="student-1",
        expected_question_numbers=[1, 2],
        paper_paths=[first, second],
        response_paths=[],
        reference_paths=[],
        output_dir=output,
        is_synthetic_student=True,
    )

    assert len(intake.materials) == 1
    assert len(intake.pages) == 1
    assert intake.pages[0].role == "paper"
    assert intake.materials[0].sha256 == hashlib.sha256(source).hexdigest()
    assert (output / intake.materials[0].source_path).read_bytes() == source
    assert first.read_bytes() == source and second.read_bytes() == source
    assert (output / "intake.json").is_file()


def test_prepare_normalizes_exif_orientation_and_keeps_stable_page_id(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    source = input_dir / "oriented.jpg"
    _write_jpeg(source, size=(80, 40), orientation=6)
    output = tmp_path / "bundle"

    intake = _intake(tmp_path, source, output)

    assert (intake.pages[0].width, intake.pages[0].height) == (40, 80)
    assert (
        intake.pages[0].image_sha256
        == hashlib.sha256((output / intake.pages[0].image_path).read_bytes()).hexdigest()
    )
    assert intake.pages[0].id.startswith("page-")


def test_pdf_renders_every_physical_page_including_blank_and_vector_grading(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    source = input_dir / "scan.pdf"
    pdf_bytes = _vector_two_page_pdf()
    source.write_bytes(pdf_bytes)
    output = tmp_path / "pdf-intake"

    intake = prepare_exam_intake(
        exam_id="exam-pdf",
        student_id="student-pdf",
        expected_question_numbers=[1],
        paper_paths=[],
        response_paths=[source],
        reference_paths=[],
        output_dir=output,
        dpi=150,
    )

    assert len(intake.pages) == 2
    assert [page.source_page for page in intake.pages] == [1, 2]
    assert [page.role for page in intake.pages] == ["response", "response"]
    first = Image.open(output / intake.pages[0].image_path).convert("RGB")
    second = Image.open(output / intake.pages[1].image_path).convert("RGB")
    red_pixels = sum(1 for r, g, b in first.get_flattened_data() if r > 150 and g < 100 and b < 100)
    nonwhite_pixels = sum(1 for r, g, b in second.get_flattened_data() if min(r, g, b) < 245)
    assert red_pixels > 20
    assert nonwhite_pixels == 0
    assert source.read_bytes() == pdf_bytes


def test_crop_preserves_source_manifest_and_records_pixel_exact_parent_bbox(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    source = input_dir / "page.jpg"
    _write_jpeg(source, size=(3, 3))
    output = tmp_path / "bundle"
    intake = _intake(tmp_path, source, output)
    manifest_before = (output / "intake.json").read_bytes()
    page = intake.pages[0]
    region = ExamRegion(page_id=page.id, bbox=(0.2, 0.2, 0.8, 0.8), role="paper")

    updated = crop_exam_regions(intake, [region], output)

    crop = updated.pages[-1]
    assert crop.parent_page_id == page.id
    assert crop.source_page == page.source_page
    assert crop.parent_bbox == (0.0, 0.0, 1.0, 1.0)
    assert (output / crop.image_path).is_file()
    assert (output / "intake.json").read_bytes() == manifest_before
    assert updated.model_dump(mode="json") == ExamIntake.model_validate_json(
        (output / "intake-with-regions.json").read_bytes()
    ).model_dump(mode="json")


def test_crop_rejects_modified_source_copy_before_publishing(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    source = input_dir / "page.jpg"
    _write_jpeg(source)
    output = tmp_path / "bundle"
    intake = _intake(tmp_path, source, output)
    page = intake.pages[0]
    source_copy = output / intake.materials[0].source_path
    original_copy = source_copy.read_bytes()
    source_copy.write_bytes(original_copy + b"altered")

    with pytest.raises(ExamIntakeError, match="source_hash_mismatch"):
        crop_exam_regions(
            intake, [ExamRegion(page_id=page.id, bbox=(0, 0, 1, 1), role="paper")], output
        )


def test_crop_rejects_symlink_escape_and_never_touches_original_manifest(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    source = input_dir / "page.jpg"
    _write_jpeg(source)
    output = tmp_path / "bundle"
    intake = _intake(tmp_path, source, output)
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / "pages" / "crops").symlink_to(outside, target_is_directory=True)
    manifest_before = (output / "intake.json").read_bytes()

    with pytest.raises(ExamIntakeError, match="symlink_forbidden"):
        crop_exam_regions(
            intake,
            [ExamRegion(page_id=intake.pages[0].id, bbox=(0.1, 0.1, 0.9, 0.9), role="paper")],
            output,
        )

    assert (output / "intake.json").read_bytes() == manifest_before
    assert list(outside.iterdir()) == []


def test_intake_refuses_output_overwrite_and_output_inside_source_directory(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    source = input_dir / "input.jpg"
    _write_jpeg(source)
    occupied = tmp_path / "occupied"
    occupied.mkdir()

    with pytest.raises(ExamIntakeError, match="output_already_exists"):
        _intake(tmp_path, source, occupied)
    with pytest.raises(ExamIntakeError, match="separate_from_sources"):
        _intake(tmp_path, source, input_dir / "nested-output")
