from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "container_smoke", ROOT / "scripts" / "container_smoke.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_image_reference_is_single_safe_argument():
    assert smoke._validate_image("ghcr.io/example/teassistant:ci-123") == (
        "ghcr.io/example/teassistant:ci-123"
    )
    for invalid in ("", "bad image", "image\n--privileged", "-leading", "x;touch-file"):
        with pytest.raises(ValueError, match="image"):
            smoke._validate_image(invalid)


def test_temporary_compose_is_isolated_and_has_no_model_or_host_data_mounts(tmp_path):
    config = smoke._compose_text(
        "ghcr.io/example/teassistant:smoke", tmp_path / "share with space", "ephemeral-db-pass"
    )
    assert config.count('image: "ghcr.io/example/teassistant:smoke"') == 3
    assert "internal: true" in config
    assert 'DEEPSEEK_API_KEY: ""' in config
    assert 'TEASSISTANT_ALLOW_CODEX: "0"' in config
    assert "evals/private" not in config
    assert "${HOME}" not in config
    assert '"' + str(tmp_path / "share with space") + ':/smoke:rw"' in config
    assert "run_web_worker.py" in config
    worker = config.split("  worker:\n", 1)[1]
    assert "container_healthcheck.py, --role, worker" in worker
    init = config.split("  init:\n", 1)[1].split("  api:\n", 1)[0]
    assert "disable: true" in init


def test_generated_upload_is_valid_synthetic_png():
    image = Image.open(io.BytesIO(smoke._png_1x1()))
    assert image.format == "PNG"
    assert image.size == (1, 1)
    image.verify()


def test_chromium_failure_blocks_smoke_report_and_cleans_disposable_stack(tmp_path, monkeypatch):
    commands = []

    def execute(command, **_kwargs):
        commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            return "linux/arm64\n"
        if "chromium" in command:
            raise RuntimeError("synthetic Chromium launch failure")
        return ""

    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/synthetic/docker")
    monkeypatch.setattr(smoke, "_run", execute)
    monkeypatch.setattr(smoke, "_api_smoke", lambda *_a, **_k: {"synthetic": True})
    report = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="Chromium"):
        smoke.run_smoke("synthetic-arm64:local", report_path=report)
    assert not report.exists()
    assert any("down" in c and "--volumes" in c for c in commands)
    assert not any("word" in c or "latex" in c for c in commands)


def test_chromium_fixture_refuses_missing_executable(tmp_path, monkeypatch):
    fixture_spec = importlib.util.spec_from_file_location(
        "browser_fixture", ROOT / "scripts/container_smoke_fixture.py"
    )
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    monkeypatch.delenv("TEASSISTANT_CHROMIUM", raising=False)
    monkeypatch.setattr(fixture.shutil, "which", lambda _name: None)
    output = tmp_path / "browser-output"
    with pytest.raises(RuntimeError, match="Chromium executable"):
        fixture.render_chromium(output)
    assert not output.exists()


def test_command_runner_uses_argv_without_shell(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    assert smoke._run(["docker", "compose", "ps"], timeout=2) == "ok"
    assert captured["command"] == ["docker", "compose", "ps"]
    assert "shell" not in captured["kwargs"]


def test_existing_report_is_rejected_before_any_docker_call(tmp_path):
    existing = tmp_path / "report.json"
    existing.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="report path must be new"):
        smoke.run_smoke(
            "teassistant:smoke", docker_executable="missing-docker", report_path=existing
        )
    assert existing.read_text(encoding="utf-8") == "keep"
