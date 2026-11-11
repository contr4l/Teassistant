"""Export both print layouts from one immutable, previously selected practice snapshot."""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bank_practice import load_practice_bank  # noqa: E402
from app.bank_practice_contracts import BankPracticeSnapshot  # noqa: E402
from app.contracts import Curriculum, StudentProgress  # noqa: E402
from app.semantic_bank import canonical_hash  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "bank", "semantic", "progress", "output", "soffice"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    for protected in (args.bank.resolve(), args.semantic.resolve(), args.snapshot.resolve().parent):
        if output.resolve().is_relative_to(protected) or protected.is_relative_to(output.resolve()):
            parser.error("comparison output must be separate from all input directories")
    if output.exists() or output.is_symlink():
        parser.error("output must not already exist")
    snapshot_bytes = args.snapshot.read_bytes()
    snapshot = BankPracticeSnapshot.model_validate_json(snapshot_bytes)
    progress_bytes = args.progress.read_bytes()
    progress = StudentProgress.model_validate_json(progress_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        from app.source_exporting import export_bank_snapshot

        results = {}
        for layout in ("answer_space", "compact"):
            if args.progress.read_bytes() != progress_bytes:
                raise ValueError("student progress changed during layout comparison")
            if args.snapshot.read_bytes() != snapshot_bytes:
                raise ValueError("input snapshot changed during layout comparison")
            bank = load_practice_bank(args.bank, args.semantic)
            curriculum = Curriculum.model_validate_json(
                (args.semantic / "curriculum.json").read_bytes()
            )
            result = export_bank_snapshot(
                snapshot,
                stage / layout,
                bank_dir=args.bank,
                practice_bank=bank,
                curriculum=curriculum,
                current_progress=progress,
                soffice_executable=args.soffice,
                layout=layout,
            )
            if result["snapshot_id"] != canonical_hash(snapshot.model_dump(mode="json")):
                raise ValueError("exporter returned a different snapshot")
            results[layout] = {
                "snapshot_id": result["snapshot_id"],
                "counts": result["counts"],
                "files": {
                    name: str(Path(layout) / Path(path).name)
                    for name, path in result["files"].items()
                },
            }
        if args.progress.read_bytes() != progress_bytes:
            raise ValueError("student progress changed before comparison publication")
        if args.snapshot.read_bytes() != snapshot_bytes:
            raise ValueError("input snapshot changed before comparison publication")
        summary = {
            "schema_version": "exam-layout-comparison-v1",
            "snapshot_id": canonical_hash(snapshot.model_dump(mode="json")),
            "source_snapshot_file_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "is_synthetic_student": snapshot.is_synthetic_student,
            "item_count": len(snapshot.items),
            "layouts": results,
        }
        (stage / "comparison.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "snapshot.json").write_bytes(snapshot_bytes)
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved private comparison: {output}")


if __name__ == "__main__":
    main()
