#!/usr/bin/env python3
"""Run the offline, manifest-first P0 evaluator. It never calls a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evaluation import EvaluationManifest, PredictionSet, evaluate  # noqa: E402


def main() -> int:
    fixtures = ROOT / "evals" / "fixtures"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=fixtures / "manifest.json")
    parser.add_argument("--predictions", type=Path, default=fixtures / "predictions.json")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    manifest = EvaluationManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    predictions = PredictionSet.model_validate_json(args.predictions.read_text(encoding="utf-8"))
    report = evaluate(manifest, predictions)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
