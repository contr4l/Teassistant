"""Render two LaTeX layouts from one previously selected, immutable snapshot."""

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
    for name in ("snapshot", "bank", "semantic", "progress", "output", "xelatex"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    for source in (args.bank, args.semantic, args.snapshot.parent, args.progress.parent):
        protected = source.resolve(strict=True)
        if output.resolve().is_relative_to(protected) or protected.is_relative_to(output.resolve()):
            parser.error("output must be separate from all input directories")
    if output.exists() or output.is_symlink():
        parser.error("output must not already exist")
    snapshot_bytes = args.snapshot.read_bytes()
    progress_bytes = args.progress.read_bytes()
    snapshot = BankPracticeSnapshot.model_validate_json(snapshot_bytes)
    progress = StudentProgress.model_validate_json(progress_bytes)
    from app.latex_exporting import export_latex_snapshot
    from app.math_latex import build_formula_catalog

    catalog = build_formula_catalog(snapshot, args.bank)
    unsupported = [key for key, entry in catalog.entries.items() if entry.status != "converted"]
    if unsupported:
        # Diagnostic only, explicitly no successful PDFs published.
        print(
            json.dumps(
                {"unsupported": {key: catalog.entries[key].errors for key in unsupported}},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise ValueError(f"native formulas unsupported: {len(unsupported)} unique; export refused")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        results = {}
        for layout in ("answer_space", "compact"):
            if args.progress.read_bytes() != progress_bytes:
                raise ValueError("student progress changed during LaTeX export")
            if args.snapshot.read_bytes() != snapshot_bytes:
                raise ValueError("input snapshot changed during LaTeX export")
            bank = load_practice_bank(args.bank, args.semantic)
            curriculum = Curriculum.model_validate_json(
                (args.semantic / "curriculum.json").read_bytes()
            )
            result = export_latex_snapshot(
                snapshot,
                stage / layout,
                bank_dir=args.bank,
                practice_bank=bank,
                curriculum=curriculum,
                current_progress=progress,
                formula_catalog=catalog,
                xelatex_executable=args.xelatex,
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
        if (
            args.progress.read_bytes() != progress_bytes
            or args.snapshot.read_bytes() != snapshot_bytes
        ):
            raise ValueError("input changed before LaTeX export publication")
        summary = {
            "schema_version": "latex-layout-comparison-v1",
            "snapshot_id": canonical_hash(snapshot.model_dump(mode="json")),
            "source_snapshot_file_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "is_synthetic_student": snapshot.is_synthetic_student,
            "item_count": len(snapshot.items),
            "unique_formula_count": len(catalog.entries),
            "layouts": results,
        }
        (stage / "comparison.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "formula-catalog.json").write_text(
            catalog.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved private LaTeX comparison: {output}")


if __name__ == "__main__":
    main()
