"""Independent offline acceptance checks for the in-container HTTP smoke path."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "container_smoke_acceptance", ROOT / "scripts" / "container_smoke.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)

USERNAME = "smoke-SYNTHETIC_USER"
PASSWORD = "SYNTHETIC_PASSWORD_DO_NOT_LOG"
CLIENT_RESULT = {
    "login": "passed",
    "student_create": "passed",
    "uploaded_synthetic_assets": 2,
    "queued_job_worker_terminal_status": "failed",
    "feedback_write_read": "passed",
    "model_call": "not attempted; no credential and internal-only network",
}


def _response(result=None):
    return json.dumps({"ok": True, "result": result or CLIENT_RESULT})


def _drive_api_smoke(monkeypatch, output):
    calls = []
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: "SYNTHETIC_USER")
    monkeypatch.setattr(smoke.secrets, "token_urlsafe", lambda _size: PASSWORD)

    def run(command, *, timeout, input_text=None):
        calls.append((list(command), timeout, input_text))
        if command[-1:] == [smoke._API_SMOKE_CLIENT_SCRIPT]:
            if isinstance(output, Exception):
                raise output
            return output
        return ""

    monkeypatch.setattr(smoke, "_run", run)
    result = smoke._api_smoke(["/synthetic/docker", "compose", "--file", "/tmp/smoke.yaml"], 30)
    return result, calls


def test_compose_keeps_http_smoke_internal_without_published_ports_or_host_socket(tmp_path):
    config = smoke._compose_text("synthetic:local", tmp_path / "share", "SYNTHETIC_DB_PASSWORD")
    assert "internal: true" in config
    assert "ports:" not in config
    assert "/var/run/docker.sock" not in config
    assert "network_mode:" not in config
    assert "host.docker.internal" not in config


def test_http_client_stays_on_container_loopback_and_http_error_body_is_not_exposed(monkeypatch):
    urls = []

    class Ready:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            urls.append(url)
            assert url.startswith("http://127.0.0.1:8000/")
            if url.endswith("/api/auth/login"):
                raise urllib.error.HTTPError(
                    url,
                    403,
                    "synthetic forbidden",
                    {},
                    io.BytesIO(b"SYNTHETIC_HTTP_BODY_SECRET\n::error::injected"),
                )
            return Ready()

    monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *_args: Opener())
    with pytest.raises(RuntimeError, match="synthetic admin login failed") as failure:
        smoke._api_smoke_client(30, USERNAME, PASSWORD)
    assert len(urls) == 2
    assert all(url.startswith("http://127.0.0.1:8000/") for url in urls)
    assert "SYNTHETIC_HTTP_BODY_SECRET" not in str(failure.value)
    assert "::error::" not in str(failure.value)
    assert str(failure.value) in smoke._SAFE_FAILURE_HINTS


def test_http_workflow_receives_credentials_only_over_stdin_and_runs_all_checks(monkeypatch):
    report, calls = _drive_api_smoke(monkeypatch, _response())
    assert report["login"] == "passed"
    assert report["student_create"] == "passed"
    assert report["uploaded_synthetic_assets"] == 2
    assert report["queued_job_worker_terminal_status"] == "failed"
    assert report["feedback_write_read"] == "passed"
    assert report["model_call"] == "not attempted; no credential and internal-only network"

    admin_calls = [call for call in calls if "create-admin" in call[0]]
    client_calls = [call for call in calls if call[0][-1:] == [smoke._API_SMOKE_CLIENT_SCRIPT]]
    assert len(admin_calls) == len(client_calls) == 1
    assert "--no-deps" in admin_calls[0][0]
    assert "--no-deps" not in client_calls[0][0]
    assert "-T" in client_calls[0][0] and "api" in client_calls[0][0]
    health_roles = {
        command[-1] for command, _timeout, _stdin in calls
        if "/app/scripts/container_healthcheck.py" in command
    }
    assert health_roles == {"api", "worker"}
    assert any("private build content present" in command[-1] for command, *_rest in calls)
    for command, _timeout, _stdin in calls:
        assert PASSWORD not in " ".join(command)
        assert "port" not in command
    assert admin_calls[0][2] == PASSWORD + "\n" + PASSWORD + "\n"
    payload = json.loads(client_calls[0][2])
    assert payload == {"timeout_seconds": 30, "username": USERNAME, "password": PASSWORD}


@pytest.mark.parametrize(
    "child_output",
    [
        "SYNTHETIC_PASSWORD_DO_NOT_LOG\n::error::injected",
        '{"ok":true,"result":{}}',
        json.dumps({"ok": True, "result": {**CLIENT_RESULT, "uploaded_synthetic_assets": 2.0}}),
        json.dumps({
            "ok": False,
            "error_type": "RuntimeError",
            "hint": "SYNTHETIC_PASSWORD_DO_NOT_LOG\n::error::injected",
        }),
    ],
)
def test_malformed_or_untrusted_child_responses_fail_closed(monkeypatch, child_output):
    with pytest.raises(RuntimeError) as failure:
        _drive_api_smoke(monkeypatch, child_output)
    assert PASSWORD not in str(failure.value)
    assert "::error::injected" not in str(failure.value)


def test_allowlisted_child_http_failure_preserves_only_fixed_hint(monkeypatch):
    output = json.dumps({
        "ok": False,
        "error_type": "RuntimeError",
        "hint": "synthetic admin login failed",
    })
    with pytest.raises(smoke.SmokeStageError, match="stage=in-container HTTP workflow") as failure:
        _drive_api_smoke(monkeypatch, output)
    assert "hint=synthetic admin login failed" in str(failure.value)
    assert PASSWORD not in str(failure.value)


def test_child_nonzero_exit_fails_closed_without_command_output(monkeypatch):
    error = smoke.SmokeCommandError(23, ("ValueError",))
    with pytest.raises(smoke.SmokeStageError, match="stage=in-container HTTP workflow.*exit=23") as failure:
        _drive_api_smoke(monkeypatch, error)
    assert PASSWORD not in str(failure.value)
    assert "SYNTHETIC" not in str(failure.value)
