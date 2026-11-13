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
    assert "ports:" not in config
    assert 'DEEPSEEK_API_KEY: ""' in config
    assert 'TEASSISTANT_ALLOW_CODEX: "0"' in config
    assert "evals/private" not in config
    assert "${HOME}" not in config
    assert str(tmp_path / "share with space") not in config
    assert ":/smoke" not in config
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


def test_internal_http_client_runs_real_routes_store_and_no_key_worker(tmp_path, monkeypatch):
    import urllib.error
    from urllib.parse import urlsplit

    from app.web_api import create_app
    from app.web_jobs import run_once
    from app.web_settings import WebSettings
    from app.web_store import Store
    from fastapi.testclient import TestClient

    front = tmp_path / "frontend"
    front.mkdir()
    (front / "index.html").write_text("<html>synthetic smoke</html>")
    config = {
        "TEASSISTANT_DATABASE_URL": f"sqlite:///{tmp_path / 'synthetic.db'}",
        "TEASSISTANT_DATA_DIR": str(tmp_path / "private"),
        "TEASSISTANT_CURRICULUM": str(ROOT / "data/curricula/junior-math-2022-draft-v2.json"),
        "TEASSISTANT_FRONTEND_DIST": str(front),
        "TEASSISTANT_MODEL_PROVIDER": "deepseek",
        "TEASSISTANT_ALLOW_CODEX": "0",
        "DEEPSEEK_API_KEY": "",
    }
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    settings = WebSettings.from_env()
    store = Store(settings.database_url)
    store.init()
    owner = store.create_user("synthetic-smoke", "synthetic-only-password", "合成管理员", "admin")
    dispatched = []

    class Reply:
        def __init__(self, response):
            self.status = response.status_code
            self.content = response.content

        def read(self):
            return self.content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with TestClient(create_app(settings=settings, store=store)) as client:
        class Opener:
            def open(self, request, timeout=None):
                url = request if isinstance(request, str) else request.full_url
                parsed = urlsplit(url)
                assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:8000"
                if parsed.path.startswith("/api/jobs/"):
                    dispatched.append(run_once(store, settings, "synthetic-offline-worker"))
                method = "GET" if isinstance(request, str) else request.get_method()
                headers = {} if isinstance(request, str) else dict(request.header_items())
                content = None if isinstance(request, str) else request.data
                response = client.request(method, parsed.path, headers=headers, content=content)
                if response.status_code >= 400:
                    raise urllib.error.HTTPError(url, response.status_code, "synthetic", {}, None)
                return Reply(response)

        monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *_args: Opener())
        monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
        report = smoke._api_smoke_client(30, "synthetic-smoke", "synthetic-only-password")

    assert report["uploaded_synthetic_assets"] == 2
    assert report["queued_job_worker_terminal_status"] == "failed"
    assert report["feedback_write_read"] == "passed"
    assert any(dispatched)
    practices = store.list_records("practice", owner["id"])
    assert len(practices) == 1
    assert practices[0]["data"]["snapshot"]["is_synthetic"] is True
    feedbacks = store.list_records("feedback", owner["id"], parent_id=practices[0]["id"])
    assert len(feedbacks) == 1 and feedbacks[0]["data"]["quality"] == 3
    store.close()


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


@pytest.mark.parametrize("error_type", [RuntimeError, KeyError, IndexError, AttributeError])
def test_failure_annotation_reports_stage_without_exception_payload(monkeypatch, capsys, error_type):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    def fail(*_args, **_kwargs):
        def operation():
            raise error_type("SYNTHETIC_SECRET\n::error::injected")
        smoke._at_stage("web workflow", operation)

    monkeypatch.setattr(smoke, "run_smoke", fail)
    assert smoke.main(["--image", "synthetic:local"]) == 2
    output = capsys.readouterr().err
    assert "::error title=Container smoke failure::stage=web workflow" in output
    assert error_type.__name__ in output
    assert "SYNTHETIC_SECRET" not in output and "injected" not in output


def test_cleanup_failure_preserves_original_stage(monkeypatch, tmp_path, capsys):
    def execute(command, **_kwargs):
        if command[1:3] == ["image", "inspect"]:
            return "linux/arm64\n"
        if "down" in command:
            raise RuntimeError("SYNTHETIC_CLEANUP_SECRET")
        if "up" in command:
            raise smoke.SmokeCommandError(17)
        return ""

    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/synthetic/docker")
    monkeypatch.setattr(smoke, "_run", execute)
    report = tmp_path / "report.json"
    with pytest.raises(smoke.SmokeStageError, match="stage=service startup.*exit=17"):
        smoke.run_smoke("synthetic:local", report_path=report)
    assert not report.exists()
    diagnostic = capsys.readouterr().err
    assert "stage=cleanup" in diagnostic and "SYNTHETIC_CLEANUP_SECRET" not in diagnostic


def test_known_static_failure_hint_is_reported_without_command_output():
    error = smoke.SmokeStageError("web workflow", RuntimeError("synthetic admin login failed"))
    assert "hint=synthetic admin login failed" in error.summary
    error = smoke.SmokeStageError("service startup", smoke.SmokeCommandError(9))
    assert error.summary == "stage=service startup; error=SmokeCommandError; exit=9"


def test_nested_stage_retains_specific_failure():
    def inner():
        smoke._at_stage("synthetic practice seed", lambda: (_ for _ in ()).throw(
            smoke.SmokeCommandError(1)
        ))

    with pytest.raises(smoke.SmokeStageError, match="stage=synthetic practice seed") as error:
        smoke._at_stage("web workflow", inner)
    assert "stage=web workflow" not in error.value.summary


def test_command_diagnostic_only_extracts_whitelisted_error_names(monkeypatch):
    class Failed:
        returncode = 1
        stdout = "SYNTHETIC_PASSWORD\nTraceback:\nsqlalchemy.exc.OperationalError: PRIVATE_DSN\n"

    monkeypatch.setattr(smoke.subprocess, "run", lambda *_args, **_kwargs: Failed())
    with pytest.raises(smoke.SmokeCommandError) as error:
        smoke._run(["synthetic-docker"], timeout=2)
    diagnostic = smoke.SmokeStageError("synthetic practice seed", error.value).summary
    assert "markers=OperationalError" in diagnostic
    assert "SYNTHETIC_PASSWORD" not in diagnostic and "PRIVATE_DSN" not in diagnostic
    arbitrary = smoke.SmokeCommandError(1, ("secret\n::error::injected",))
    assert arbitrary.markers == ()


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
