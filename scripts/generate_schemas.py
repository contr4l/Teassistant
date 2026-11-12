"""Generate shared P0 contracts; no application server or paid service required."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bank_practice_contracts import BankPracticeSnapshot, PracticeBank  # noqa: E402
from app.contracts import Curriculum, PracticeRequest, PracticeSnapshot, Question, StudentProgress  # noqa: E402
from app.exam_contracts import (  # noqa: E402
    ExamAnalysis,
    ExamCorrection,
    ExamIntake,
    ExamModelResponse,
    LearningSummary,
)


def main():
    target = ROOT / "contracts" / "p0-v1"
    target.mkdir(parents=True, exist_ok=True)
    for model in [Curriculum, Question, StudentProgress, PracticeRequest, PracticeSnapshot]:
        (target / f"{model.__name__}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Generated {5} P0 schemas at {target}")
    target = ROOT / "contracts" / "source-practice-v1"
    target.mkdir(parents=True, exist_ok=True)
    for model in [PracticeBank, BankPracticeSnapshot]:
        (target / f"{model.__name__}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Generated {2} source practice schemas at {target}")
    target = ROOT / "contracts" / "exam-analysis-v1"
    target.mkdir(parents=True, exist_ok=True)
    models = [ExamIntake, ExamModelResponse, ExamAnalysis, ExamCorrection, LearningSummary]
    for model in models:
        (target / f"{model.__name__}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Generated {len(models)} exam analysis schemas at {target}")


if __name__ == "__main__":
    main()
