"""Explicit additive-catalog migration with immutable model-response provenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts import Curriculum  # noqa: E402
from app.semantic_migration import merge_semantic_runs  # noqa: E402
from app.semantic_view import render_semantic_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-run", type=Path, required=True)
    parser.add_argument("--reassessed-run", type=Path, required=True)
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--affected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = merge_semantic_runs(
            args.base_run,
            args.reassessed_run,
            args.bank,
            args.inputs,
            args.affected,
            args.output,
        )
        rows = json.loads((args.output / "annotations.json").read_text())["questions"]
        curriculum = Curriculum.model_validate_json((args.output / "curriculum.json").read_bytes())
        inputs = {
            item["question_id"]: item for item in json.loads(args.inputs.read_bytes())["questions"]
        }
        render_semantic_report(args.output, rows, curriculum, inputs, args.inputs.resolve().parent)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Semantic migration failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 3 if summary.get("failed", 0) or summary.get("not_attempted", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
