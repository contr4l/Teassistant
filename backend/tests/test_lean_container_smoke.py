from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "lean_container_smoke", ROOT / "scripts" / "container_smoke.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_api_image_reference_is_rejected_before_docker_is_called(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Docker must not be called for an invalid API image")

    monkeypatch.setattr(smoke, "_run", fail_if_called)
    with pytest.raises(ValueError, match="image"):
        smoke.run_smoke("worker:local", api_image="bad image", docker_executable="missing")


def test_mixed_images_must_have_matching_platforms(monkeypatch):
    commands = []

    def inspect(command, **_kwargs):
        commands.append(command)
        return "linux/arm64\n" if command[-1] == "worker:local" else "linux/amd64\n"

    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/synthetic/docker")
    monkeypatch.setattr(smoke, "_run", inspect)
    with pytest.raises(ValueError, match="same platform"):
        smoke.run_smoke("worker:local", api_image="api:local")
    assert len(commands) == 2
    assert all(command[1:3] == ["image", "inspect"] for command in commands)


def test_compose_routes_api_init_and_worker_to_the_intended_images(tmp_path):
    config = smoke._compose_text(
        "worker:full", tmp_path / "share", "temporary-db-password", api_image="api:lean"
    )
    init = config.split("  init:\n", 1)[1].split("  api:\n", 1)[0]
    api = config.split("  api:\n", 1)[1].split("  worker:\n", 1)[0]
    worker = config.split("  worker:\n", 1)[1].split("volumes:\n", 1)[0]
    assert 'image: "api:lean"' in init
    assert 'image: "api:lean"' in api
    assert 'image: "worker:full"' in worker
    assert config.count('image: "api:lean"') == 2
    assert config.count('image: "worker:full"') == 1


def test_browser_word_and_latex_fixtures_run_only_in_worker(monkeypatch, tmp_path):
    commands = []

    def execute(command, **_kwargs):
        commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            return "linux/arm64\n"
        if any(item.endswith("container_smoke_fixture.py") for item in command):
            fixture_index = next(
                index
                for index, item in enumerate(command)
                if item.endswith("container_smoke_fixture.py")
            )
            return json.dumps({"fixture": command[fixture_index + 1]}) + "\n"
        return ""

    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/synthetic/docker")
    monkeypatch.setattr(smoke, "_run", execute)
    monkeypatch.setattr(smoke, "_api_smoke", lambda *_args, **_kwargs: {"synthetic": True})
    smoke.run_smoke("worker:full", api_image="api:lean", report_path=tmp_path / "report.json")

    fixture_commands = [
        command
        for command in commands
        if any(item.endswith("container_smoke_fixture.py") for item in command)
    ]
    assert len(fixture_commands) == 3
    assert {
        command[
            next(
                index
                for index, item in enumerate(command)
                if item.endswith("container_smoke_fixture.py")
            )
            + 1
        ]
        for command in fixture_commands
    } == {"chromium", "word", "latex"}
    assert all(command[command.index("--no-deps") + 1] == "worker" for command in fixture_commands)


def test_synthetic_pdf_is_valid_and_has_one_page():
    import pypdfium2

    pdf = pypdfium2.PdfDocument(smoke._pdf_single_page())
    try:
        assert len(pdf) == 1
    finally:
        pdf.close()


def test_http_child_keeps_legacy_protocol_and_reports_both_uploaded_types(monkeypatch):
    legacy_result = {
        "login": "passed",
        "student_create": "passed",
        "uploaded_synthetic_assets": 2,
        "queued_job_worker_terminal_status": "failed",
        "feedback_write_read": "passed",
        "model_call": "not attempted; no credential and internal-only network",
    }

    def execute(command, **_kwargs):
        if command[-1:] == [smoke._API_SMOKE_CLIENT_SCRIPT]:
            return json.dumps({"ok": True, "result": legacy_result})
        return ""

    monkeypatch.setattr(smoke, "_run", execute)
    report = smoke._api_smoke(["/synthetic/docker", "compose"], 30)
    assert report["uploaded_asset_types"] == ["PNG", "PDF"]
    assert report["uploaded_synthetic_assets"] == 2
