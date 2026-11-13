"""Acceptance checks for smoke output ownership and temporary-volume cleanup."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "container_smoke_cleanup_acceptance", ROOT / "scripts" / "container_smoke.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def _isolated_tempdir(monkeypatch, host_tmp: Path, *, fail_cleanup=False):
    class TemporaryDirectory:
        def __init__(self, prefix):
            self.path = host_tmp / prefix.rstrip("-")

        def __enter__(self):
            self.path.mkdir()
            return str(self.path)

        def __exit__(self, *_args):
            if fail_cleanup:
                raise PermissionError("SYNTHETIC_HOST_CLEANUP_SECRET")
            shutil.rmtree(self.path)
            return False

    monkeypatch.setattr(smoke.tempfile, "TemporaryDirectory", TemporaryDirectory)
    return host_tmp / "teassistant-container-smoke"


def _fake_docker(monkeypatch, temp_root: Path):
    calls = []
    compose_documents = []

    def run(command, *, timeout, input_text=None):
        command = list(command)
        calls.append((command, timeout, input_text))
        if command[1:3] == ["image", "inspect"]:
            return "linux/arm64\n"
        if "--file" in command:
            compose_file = Path(command[command.index("--file") + 1])
            document = compose_file.read_text(encoding="utf-8")
            compose_documents.append(document)
            assert not (temp_root / "share").exists()
            assert str(temp_root) not in document
        fixture_script = next(
            (arg for arg in command if arg.endswith("container_smoke_fixture.py")), None
        )
        if fixture_script:
            fixture = command[command.index(fixture_script) + 1]
            output = command[command.index("--output") + 1]
            assert output.startswith("/app/work/container-smoke/")
            return json.dumps({"status": "passed", "kind": fixture})
        return ""

    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/synthetic/docker")
    monkeypatch.setattr(smoke, "_run", run)
    monkeypatch.setattr(smoke, "_api_smoke", lambda *_args, **_kwargs: {"api_ready": True})
    return calls, compose_documents


def test_smoke_outputs_use_named_volume_and_cleanup_removes_it(tmp_path, monkeypatch):
    temp_root = _isolated_tempdir(monkeypatch, tmp_path)
    calls, documents = _fake_docker(monkeypatch, temp_root)

    report = smoke.run_smoke("synthetic-arm64:local", report_path=tmp_path / "report.json")

    assert report["synthetic_chromium_render"]["kind"] == "chromium"
    assert report["synthetic_word_export"]["kind"] == "word"
    assert report["synthetic_latex_export"]["kind"] == "latex"
    assert documents
    for document in documents:
        assert "smoke_data:/app/work" in document
        assert "smoke_db:/var/lib/postgresql/data" in document
        assert "/var/run/docker.sock" not in document
        assert "ports:" not in document

    outputs = {
        command[command.index(next(arg for arg in command if arg.endswith("container_smoke_fixture.py"))) + 1]:
            command[command.index("--output") + 1]
        for command, _timeout, _stdin in calls
        if any(arg.endswith("container_smoke_fixture.py") for arg in command)
    }
    assert outputs == {
        "chromium": "/app/work/container-smoke/chromium-render",
        "word": "/app/work/container-smoke/word-export",
        "latex": "/app/work/container-smoke/latex-export",
    }
    teardown = [command for command, _timeout, _stdin in calls if "down" in command]
    assert len(teardown) == 1
    assert teardown[0][-3:] == ["down", "--volumes", "--remove-orphans"]
    assert not temp_root.exists()


def test_host_temp_cleanup_permission_error_is_annotated_without_payload(
    tmp_path, monkeypatch, capsys
):
    temp_root = _isolated_tempdir(monkeypatch, tmp_path, fail_cleanup=True)
    calls, _documents = _fake_docker(monkeypatch, temp_root)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    exit_code = smoke.main([
        "--image", "synthetic-arm64:local",
        "--report", str(tmp_path / "report.json"),
    ])

    assert exit_code == 2
    assert not (tmp_path / "report.json").exists()
    output = capsys.readouterr()
    diagnostic = output.err
    assert "::error title=Container smoke failure::stage=host lifecycle; error=PermissionError" in diagnostic
    assert "SYNTHETIC_HOST_CLEANUP_SECRET" not in diagnostic
    assert output.out == ""
    teardown = [command for command, _timeout, _stdin in calls if "down" in command]
    assert len(teardown) == 1
    assert teardown[0][-3:] == ["down", "--volumes", "--remove-orphans"]
