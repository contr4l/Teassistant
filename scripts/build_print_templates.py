"""Build the three empty, versioned DOCX templates used by source export."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.print_layout import TEMPLATE_VERSION, _build_template  # noqa: E402


def main() -> None:
    template_dir = ROOT / "templates" / TEMPLATE_VERSION
    template_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "student-answer-space.docx": _build_template(
            teacher=False, layout="answer_space", is_synthetic_student=False
        ),
        "student-compact.docx": _build_template(
            teacher=False, layout="compact", is_synthetic_student=False
        ),
        "teacher.docx": _build_template(
            teacher=True, layout="answer_space", is_synthetic_student=False
        ),
    }
    for name, document in outputs.items():
        path = template_dir / name
        document.save(path)
        print(f"Wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
