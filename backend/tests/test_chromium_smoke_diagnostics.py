"""Acceptance tests for narrowly scoped Chromium fixture diagnostics."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "container_smoke_chromium_diagnostics", ROOT / "scripts" / "container_smoke.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)

FIXTURE = "/app/scripts/container_smoke_fixture.py"
CHROMIUM_COMMAND = [
    "docker", "compose", "run", "--rm", "-T", "--no-deps", "api",
    "/app/.venv/bin/python", FIXTURE, "chromium", "--output",
    "/app/work/container-smoke/chromium-render",
]


def _failed_process(monkeypatch, output):
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, stdout=output),
    )


def test_diagnostic_only_emits_for_github_chromium_fixture_failure(monkeypatch, capsys):
    _failed_process(monkeypatch, "synthetic chromium failure")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(smoke.SmokeCommandError):
        smoke._run(CHROMIUM_COMMAND, timeout=90)

    diagnostic = capsys.readouterr().err
    assert diagnostic.startswith("::error title=Chromium synthetic diagnostic::")
    assert "synthetic chromium failure" in diagnostic


@pytest.mark.parametrize(
    "command",
    [
        ["docker", "compose", "exec", "-T", "api", "python", "-c", "synthetic"],
        ["docker", "compose", "run", "api", FIXTURE, "word", "--output", "/tmp/out"],
        ["docker", "compose", "run", "api", FIXTURE, "chromium-extra", "--output", "/tmp/out"],
    ],
)
def test_other_child_failures_never_emit_raw_output(monkeypatch, capsys, command):
    _failed_process(monkeypatch, "SYNTHETIC_COMMAND_SECRET\n::error::injected")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(smoke.SmokeCommandError):
        smoke._run(command, timeout=90)

    assert capsys.readouterr().err == ""


def test_chromium_output_is_suppressed_outside_github_actions(monkeypatch, capsys):
    _failed_process(monkeypatch, "SYNTHETIC_LOCAL_DIAGNOSTIC")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    with pytest.raises(smoke.SmokeCommandError):
        smoke._run(CHROMIUM_COMMAND, timeout=90)

    assert capsys.readouterr().err == ""


def test_environment_secrets_dsn_urls_paths_and_sensitive_assignments_are_redacted(
    monkeypatch, capsys
):
    monkeypatch.setenv("CI_SECRET", "ENV_SECRET_VALUE_9f73")
    monkeypatch.setenv("APP_ACCESS_TOKEN", "ENV_TOKEN_VALUE_57ba")
    monkeypatch.setenv("TEASSISTANT_DATABASE_URL", "postgresql://dbuser:dbpass@db/private")
    monkeypatch.setenv("PRIVATE_DSN", "opaque_environment_dsn_value")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = (
        "ENV_SECRET_VALUE_9f73 ENV_TOKEN_VALUE_57ba "
        "postgresql://dbuser:dbpass@db/private opaque_environment_dsn_value "
        "dsn=OPAQUE_LOG_DSN_SECRET database_url=OPAQUE_LOG_URL_SECRET "
        "token=OPAQUE_LOG_TOKEN_SECRET password='OPAQUE_LOG_PASSWORD_SECRET' "
        "api_key=OPAQUE_LOG_KEY_SECRET https://example.invalid/private/report "
        "/Users/example/private/report.json"
    )

    smoke._emit_chromium_fixture_diagnostic(output)

    diagnostic = capsys.readouterr().err
    for secret in (
        "ENV_SECRET_VALUE_9f73", "ENV_TOKEN_VALUE_57ba", "dbuser:dbpass",
        "opaque_environment_dsn_value", "OPAQUE_LOG_DSN_SECRET",
        "OPAQUE_LOG_URL_SECRET", "OPAQUE_LOG_TOKEN_SECRET",
        "OPAQUE_LOG_PASSWORD_SECRET", "OPAQUE_LOG_KEY_SECRET",
        "/Users/example/private/report.json",
    ):
        assert secret not in diagnostic
    assert "<url>" in diagnostic


def test_secret_crossing_tail_boundary_does_not_leak_a_suffix(monkeypatch, capsys):
    monkeypatch.setenv("CI_SECRET", "BOUNDARY_SECRET_" + ("x" * 100))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    secret = "BOUNDARY_SECRET_" + ("x" * 100)
    output = ("ordinary diagnostic " * 4000) + secret

    smoke._emit_chromium_fixture_diagnostic(output)

    diagnostic = capsys.readouterr().err
    assert secret not in diagnostic
    assert "x" * 40 not in diagnostic


def test_annotation_escapes_command_injection(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = "first%line\r\n::error title=Injected::second command"

    smoke._emit_chromium_fixture_diagnostic(output)

    diagnostic = capsys.readouterr().err
    assert diagnostic.startswith("::error title=Chromium synthetic diagnostic::")
    assert len(diagnostic.splitlines()) == 1
    assert "%25" in diagnostic and "%0D" in diagnostic and "%0A" in diagnostic
    assert "%0A::error title=Injected::" in diagnostic


def test_annotation_is_bounded_after_encoding(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    smoke._emit_chromium_fixture_diagnostic("%" * 6000)
    diagnostic = capsys.readouterr().err
    assert diagnostic.startswith("::error title=Chromium synthetic diagnostic::")
    assert len(diagnostic.encode("utf-8")) <= 5200


def test_failure_still_raises_nonzero_command_error_after_annotation(monkeypatch, capsys):
    _failed_process(monkeypatch, "synthetic chromium fixture failed")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(smoke.SmokeCommandError) as failure:
        smoke._run(CHROMIUM_COMMAND, timeout=90)

    assert failure.value.exit_code == 1
    assert capsys.readouterr().err.startswith("::error title=Chromium synthetic diagnostic::")
