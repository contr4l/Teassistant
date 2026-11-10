"""Live runner dispatch is tested with stubs; tests never call either service."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.ai import CallResult
from app.contracts import ContentNode, Question, SourceRegion
from PIL import Image

spec = importlib.util.spec_from_file_location(
    "live_extract", Path(__file__).resolve().parents[2] / "scripts/live_extract.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
ROOT_SCRIPT = Path(spec.origin)


def inputs(tmp_path):
    image = tmp_path / "question.png"
    Image.new("RGB", (20, 20), "white").save(image)
    curriculum = tmp_path / "curriculum.json"
    curriculum.write_text(
        json.dumps(
            {"version": "demo", "is_synthetic": True, "points": [{"id": "p", "name": "demo"}]}
        )
    )
    return [
        "--image",
        str(image),
        "--curriculum",
        str(curriculum),
        "--output",
        str(tmp_path / "result.json"),
    ]


def fake_adapter(provider):
    class Adapter:
        def __init__(self, config):
            pass

        async def extract(self, **kwargs):
            assert kwargs["allowed_point_ids"] == {"p"}
            assert kwargs["image_data_url"].startswith("data:image/png;base64,")
            q = Question(
                id="q",
                canonical_id="q",
                content_fingerprint="fp",
                curriculum_version="demo",
                stem=[ContentNode(kind="text", text="1+1")],
                answer=[ContentNode(kind="text", text="2")],
                point_ids=["p"],
                difficulty_score=10,
                sources=[SourceRegion(asset_id="a", page=1, bbox=(0, 0, 1, 1))],
            )
            return CallResult(
                provider=provider,
                model="stub",
                model_metadata_source="test_stub",
                attempts=1,
                usage=None,
                raw_json_result={},
                question=q,
            )

    return Adapter


def test_deepseek_runner_writes_result_without_calling_service(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.DeepSeekConfig, "from_env", lambda: object())
    monkeypatch.setattr(runner, "DeepSeekAdapter", fake_adapter("deepseek"))
    assert runner.main(["--provider", "deepseek", *inputs(tmp_path)]) == 0
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["provider"] == "deepseek"
    assert result["scope"] == "single_question_live_extraction"
    assert result["curriculum_is_synthetic"] is True


def test_codex_runner_does_not_read_deepseek_credentials(tmp_path, monkeypatch):
    from app import codex_cli

    monkeypatch.setattr(
        runner.DeepSeekConfig, "from_env", lambda: pytest.fail("unexpected API config")
    )
    monkeypatch.setattr(codex_cli, "CodexCLIAdapter", fake_adapter("codex_cli"))
    assert runner.main(["--provider", "codex", *inputs(tmp_path)]) == 0
    assert json.loads((tmp_path / "result.json").read_text())["provider"] == "codex_cli"


def test_existing_output_fails_before_provider_creation(tmp_path, monkeypatch):
    args = inputs(tmp_path)
    (tmp_path / "result.json").write_text("keep previous result")
    monkeypatch.setattr(
        runner.DeepSeekConfig, "from_env", lambda: pytest.fail("unexpected API config")
    )
    assert runner.main(["--provider", "deepseek", *args]) == 1
    assert (tmp_path / "result.json").read_text() == "keep previous result"


def test_invalid_image_fails_before_credentials_are_read(tmp_path, monkeypatch):
    args = inputs(tmp_path)
    (tmp_path / "question.png").write_text("not an image")
    monkeypatch.setattr(
        runner.DeepSeekConfig, "from_env", lambda: pytest.fail("unexpected API config")
    )
    assert runner.main(["--provider", "deepseek", *args]) == 1
    assert not (tmp_path / "result.json").exists()


def test_provider_must_be_explicit_no_default_network_path():
    with pytest.raises(SystemExit) as error:
        runner.parser().parse_args([])
    assert error.value.code == 2


def test_codex_runner_with_fake_executable_uses_real_process_boundary(tmp_path):
    """An actual local child process, with an explicitly synthetic response and no model."""
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "assert '--sandbox' in args and args[args.index('--sandbox')+1]=='read-only'\n"
        "assert '--ephemeral' in args\n"
        "assert sys.stdin.read()\n"
        "option='--output-last-message' if '--output-last-message' in args else '-o'\n"
        "destination=Path(args[args.index(option)+1])\n"
        "destination.write_text(json.dumps({'stem':[{'kind':'text','text':'1+1'}],"
        "'answer':[{'kind':'text','text':'2'}],'solution':[],'subquestions':[],"
        "'point_ids':['p'],'prerequisite_ids':[],'difficulty_score':10,'bbox':[0,0,1,1]}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    args = inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT_SCRIPT),
            "--provider",
            "codex",
            "--codex-executable",
            str(executable),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["provider"] == "codex_cli"
    assert report["model_metadata_source"] == "cli-default-unresolved"
    assert report["usage"] is None
