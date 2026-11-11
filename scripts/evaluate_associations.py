"""Evaluate a source bank against an independently frozen question cohort."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.native_equation import extract_native_equation  # noqa: E402
from app.source_association import compare_association  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(value) -> str:
    # Only V3's additive source metadata is excluded from the frozen V2 snapshot.
    def original(item):
        if isinstance(item, dict):
            return {
                key: original(child)
                for key, child in item.items()
                if key not in {"native_equation", "prog_id"}
            }
        if isinstance(item, list):
            return [original(child) for child in item]
        return item

    return digest(
        json.dumps(
            original(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )


def pixels(content: bytes) -> tuple[tuple[int, int], str]:
    with Image.open(io.BytesIO(content)) as image:
        return image.size, digest(image.convert("RGBA").tobytes())


def evaluate(bank_dir: Path, manifest: dict) -> dict:
    """Fail closed on cohort drift; never derive the denominator from predictions."""
    bank_dir = bank_dir.resolve(strict=True)
    bank = json.loads((bank_dir / "bank.json").read_text())
    expected = manifest["questions"]
    denominator = manifest["denominator"]
    minimum = manifest["minimum_confirmed"]
    minimum_rate = manifest["minimum_rate"]
    expected_ids = [question["id"] for question in expected]
    if (
        type(denominator) is not int
        or denominator <= 0
        or len(expected_ids) != denominator
        or len(set(expected_ids)) != denominator
        or type(minimum) is not int
        or not 0 < minimum <= denominator
        or type(minimum_rate) not in {int, float}
        or not 0 < minimum_rate <= 1
    ):
        raise ValueError("invalid fixed cohort manifest")

    errors = []
    documents = {item["id"]: item for item in bank["documents"]}
    expected_sources = {item["document_id"] for item in manifest["sources"]}
    if (
        len(documents) != len(bank["documents"])
        or len(expected_sources) != len(manifest["sources"])
        or set(documents) != expected_sources
    ):
        errors.append({"error": "source document cohort differs from manifest"})
    source_cache = {}
    source_media = {}
    exported_pixels = {}
    for source in manifest["sources"]:
        try:
            document = documents[source["document_id"]]
            if any(document[key] != source[key] for key in ["sha256", "pair_id", "role"]):
                raise ValueError("source identity changed")
            path = (bank_dir / "sources" / source["filename"]).resolve(strict=True)
            path.relative_to(bank_dir)
            content = path.read_bytes()
            if digest(content) != source["sha256"] or len(content) != source["bytes"]:
                raise ValueError("source bytes changed")
            with zipfile.ZipFile(path) as archive:
                for member, key in [
                    ("word/document.xml", "document_xml_sha256"),
                    ("word/_rels/document.xml.rels", "relationships_sha256"),
                ]:
                    if digest(archive.read(member)) != source[key]:
                        raise ValueError("source XML changed")
                source_cache[source["document_id"]] = {
                    part: extract_native_equation(archive.read(part))
                    for part in archive.namelist()
                    if part.startswith("word/embeddings/") and not part.endswith("/")
                }
                source_media[source["document_id"]] = {
                    part: archive.read(part)
                    for part in archive.namelist()
                    if part.startswith("word/media/") and not part.endswith("/")
                }
        except (KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
            errors.append({"source": source["document_id"], "error": str(exc)})

    predicted = bank["questions"]
    counts = Counter(question["id"] for question in predicted)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    missing = sorted(set(expected_ids) - counts.keys())
    extra = sorted(counts.keys() - set(expected_ids))
    by_id = {question["id"]: question for question in predicted}
    pairs = {pair["id"]: pair for pair in bank["pairs"]}
    expected_pairs = {question["pair_id"] for question in expected}
    if len(pairs) != len(bank["pairs"]) or set(pairs) != expected_pairs:
        errors.append({"error": "source pair cohort differs from manifest"})
    rows = []
    for expected_question in expected:
        qid = expected_question["id"]
        row = {"id": qid, "result": "failed", "errors": []}
        rows.append(row)
        if qid in missing:
            row["result"] = "missing"
            continue
        if qid in duplicates:
            row["errors"].append("duplicate question identity")
            continue
        question = by_id[qid]
        try:
            for key in ["pair_id", "number", "source_question_range", "analysis_question_range"]:
                if question[key] != expected_question[key]:
                    raise ValueError(f"changed {key}")
            pair = pairs[question["pair_id"]]
            for role in ["original", "analysis"]:
                key = f"{role}_document_id"
                if pair[key] != expected_question[key]:
                    raise ValueError(f"changed {key}")
            for blocks_key, hash_key in [
                ("stem_blocks", "source_stem_snapshot_sha256"),
                ("analysis_stem_blocks", "analysis_stem_snapshot_sha256"),
                ("answer_blocks", "answer_snapshot_sha256"),
            ]:
                if snapshot(question[blocks_key]) != expected_question[hash_key]:
                    raise ValueError(f"changed {blocks_key}")
                for block in question[blocks_key]:
                    for occurrence in block.get("media_occurrences", []):
                        source_image = source_media[block["document_id"]][occurrence["source_part"]]
                        image_path = (bank_dir / occurrence["media_path"]).resolve(strict=True)
                        image_path.relative_to(bank_dir)
                        if image_path not in exported_pixels:
                            exported_pixels[image_path] = pixels(image_path.read_bytes())
                        if pixels(source_image) != exported_pixels[image_path]:
                            raise ValueError("rendered image differs from source pixels")
                    for ole in block.get("ole_occurrences", []):
                        actual = source_cache[block["document_id"]][ole["source_part"]]
                        if ole.get("native_equation") != actual:
                            raise ValueError("native evidence differs from source object")
            if (
                question["structure_status"] != "imported"
                or question["answer_available"] is not True
            ):
                raise ValueError("incomplete question or answer")
            status, _ = compare_association(
                question["stem_blocks"], question["analysis_stem_blocks"], bank_dir
            )
            if status != question["association_status"]:
                raise ValueError("reported association differs from recomputation")
            row["result"] = status
        except (KeyError, ValueError, OSError) as exc:
            row["errors"].append(str(exc))

    totals = Counter(row["result"] for row in rows)
    confirmed = totals["confirmed"]
    rate = confirmed / denominator
    integrity_ok = not (errors or missing or extra or duplicates or totals["failed"])
    return {
        "schema_version": "association-cohort-evaluation-v1",
        "denominator": denominator,
        "minimum_confirmed": minimum,
        "minimum_rate": minimum_rate,
        "confirmed": confirmed,
        "unverified": totals["unverified"],
        "failed": totals["failed"],
        "missing": missing,
        "extra": extra,
        "duplicate": duplicates,
        "confirmed_rate": rate,
        "integrity_ok": integrity_ok,
        "source_errors": errors,
        "passed": integrity_ok and confirmed >= minimum and rate >= minimum_rate,
        "questions": rows,
        "scope": "source association only; not mathematical correctness or practice readiness",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        content = args.manifest.read_bytes()
        if digest(content) != args.manifest_sha256:
            raise ValueError("manifest hash differs from frozen cohort")
        report = evaluate(args.bank, json.loads(content))
        report["manifest_sha256"] = digest(content)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: value for key, value in report.items() if key != "questions"}))
        return 0 if report["passed"] else 1
    except (ValueError, OSError, KeyError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
