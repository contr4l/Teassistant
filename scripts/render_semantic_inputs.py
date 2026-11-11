#!/usr/bin/env python3
"""Render immutable source-bank questions for semantic model validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.semantic_inputs import render_inputs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--question-id", action="append", dest="question_ids")
    parser.add_argument("--chromium-executable")
    args = parser.parse_args()
    manifest = render_inputs(
        args.bank_dir,
        args.output_dir,
        args.question_ids,
        chromium_executable=args.chromium_executable,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "source_bank_sha256": manifest["source_bank_sha256"],
                "question_count": len(manifest["questions"]),
                "tile_count": sum(len(q["images"]) for q in manifest["questions"]),
                "unverified_associations": sum(
                    q["association_status"] != "confirmed" for q in manifest["questions"]
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
