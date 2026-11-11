"""Synthetic recovery tests of the local semantic runner; all model calls are mocked."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.semantic_bank import canonical_hash
from app.semantic_models import SemanticResponseError
from PIL import Image


@pytest.fixture
def setup_run(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "semantic_runner", Path(__file__).resolve().parents[2] / "scripts/annotate_question_bank.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    bank_dir, inputs_dir = tmp_path / "source", tmp_path / "inputs"
    bank_dir.mkdir()
    inputs_dir.mkdir()
    Image.new("RGB", (2, 2), "white").save(inputs_dir / "synthetic.png")
    questions = [
        {"id": key, "association_status": "confirmed", "text": "explicitly synthetic"}
        for key in ["q1", "q2"]
    ]
    content = json.dumps({"is_synthetic": True, "questions": questions}).encode()
    (bank_dir / "bank.json").write_bytes(content)
    manifest = {
        "source_bank_sha256": runner.hashlib.sha256(content).hexdigest(),
        "questions": [
            {
                "question_id": question["id"],
                "source_hash": canonical_hash(question),
                "association_status": "confirmed",
                "reliable_solution_used": True,
                "text": "explicitly synthetic",
                "images": [
                    {
                        "path": "synthetic.png",
                        "sha256": runner.hashlib.sha256(
                            (inputs_dir / "synthetic.png").read_bytes()
                        ).hexdigest(),
                        "width": 2,
                        "height": 2,
                    }
                ],
            }
            for question in questions
        ],
    }
    (inputs_dir / "inputs.json").write_text(json.dumps(manifest))
    executable = tmp_path / "synthetic-unused-cli"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    args = SimpleNamespace(
        bank=bank_dir,
        inputs=inputs_dir / "inputs.json",
        curriculum=Path(__file__).resolve().parents[2]
        / "data/curricula/junior-math-2022-draft-v1.json",
        provider="codex",
        codex_executable=str(executable),
        codex_model="synthetic-model",
        timeout=10,
        output=tmp_path / "run",
        resume=False,
        retry_failed=False,
        question_ids_file=None,
        concurrency=1,
        batch_size=2,
    )
    return runner, args


def test_invalid_model_response_is_persisted_without_losing_denominator(setup_run, monkeypatch):
    runner, args = setup_run

    class FakeAdapter:
        def __init__(self, config):
            pass

        async def assess(self, **kwargs):
            raise SemanticResponseError(
                "synthetic response invalid",
                error_code="invalid_json",
                raw_response="synthetic malformed response",
                attempts=1,
            )

    monkeypatch.setattr(runner, "CodexSemanticAdapter", FakeAdapter)
    summary = asyncio.run(runner.run(args))
    assert summary["denominator"] == 2 and summary["failed"] == 2
    assert summary["successful_response_batches"] == 0
    audit = json.loads((args.output / "calls/0001/result.json").read_text())
    assert audit["raw_response"] == "synthetic malformed response"
    assert (args.output / "curriculum.json").read_bytes() == args.curriculum.read_bytes()

    state_path = args.output / "annotations.json"
    state = json.loads(state_path.read_text())
    state["questions"]["q1"]["status"] = "running"
    state_path.write_text(json.dumps(state))
    args.resume = True
    asyncio.run(runner.run(args))
    recovered = json.loads(state_path.read_text())["questions"]
    assert recovered["q1"]["error_code"] == "interrupted_call_outcome_unknown"
    assert recovered["q1"]["status"] == "failed"
    assert len(list((args.output / "calls").iterdir())) == 1

    args.retry_failed = True
    asyncio.run(runner.run(args))
    assert len(list((args.output / "calls").iterdir())) == 2
    assert (args.output / "calls/0001/result.json").exists()
    Path(args.codex_executable).write_text("#!/bin/sh\nexit 1\n")
    with pytest.raises(ValueError, match="matching"):
        asyncio.run(runner.run(args))
    assert len(list((args.output / "calls").iterdir())) == 2


def test_changed_input_and_missing_model_reject_before_call(setup_run, monkeypatch):
    runner, args = setup_run
    args.codex_model = None
    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(runner.run(args))
    assert not args.output.exists()

    args.codex_model = "synthetic-model"
    (args.inputs.parent / "synthetic.png").write_bytes(b"changed synthetic image")
    with pytest.raises(ValueError, match="image changed"):
        asyncio.run(runner.run(args))
    assert not args.output.exists()
