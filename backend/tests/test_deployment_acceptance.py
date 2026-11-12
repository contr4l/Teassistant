"""Independent deployment counterexamples; subprocesses and services never start here."""

import importlib.util
import re
import stat
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest


def _script(name):
    spec = importlib.util.spec_from_file_location(
        f"independent_{name}", Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


container_entrypoint = _script("container_entrypoint")
container_healthcheck = _script("container_healthcheck")
deploy = _script("deploy")


def _config(tmp_path):
    env_file = tmp_path / "deployment.env"
    deploy.initialize(env_file)
    return env_file, deploy.read_config(env_file)


def test_init_refuses_existing_symlink_without_touching_target(tmp_path, capsys):
    target = tmp_path / "developer.env"
    target.write_text("SYNTHETIC_EXISTING_VALUE=keep\n")
    path = tmp_path / "deployment.env"
    path.symlink_to(target)
    assert deploy.main(["--env-file", str(path), "init"]) == 2
    assert target.read_text() == "SYNTHETIC_EXISTING_VALUE=keep\n"
    assert path.is_symlink()
    assert "SYNTHETIC_EXISTING_VALUE" not in capsys.readouterr().err


def test_init_uses_private_permissions_and_does_not_print_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deploy.secrets, "token_hex", lambda _size: "a" * 64)
    path, _ = _config(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "a" * 64 not in capsys.readouterr().out


@pytest.mark.parametrize("line", ["A=one\nA=two", "A='unfinished", "export A=secret"])
def test_malformed_config_fails_without_exposing_values(tmp_path, line):
    path = tmp_path / "bad.env"
    path.write_text(line)
    with pytest.raises(deploy.DeploymentError) as error:
        deploy.read_config(path)
    assert "secret" not in str(error.value)
    assert "unfinished" not in str(error.value)


@pytest.mark.parametrize("bad_path", ["/bankish/source", "/bank/../source", "/Users/local"])
def test_bank_mount_path_cannot_escape_container_mount(tmp_path, bad_path):
    _, values = _config(tmp_path)
    values.update(TEASSISTANT_BANK_DIR=bad_path, TEASSISTANT_SEMANTIC_DIR="/bank/semantic")
    with pytest.raises(deploy.DeploymentError):
        deploy.validate_config(values)


def test_bank_symlink_outside_mount_is_not_validated_as_available(tmp_path):
    _, values = _config(tmp_path)
    outside = tmp_path / "not-mounted"
    outside.mkdir()
    (outside / "bank.json").write_text("{}")
    root = Path(values["TEASSISTANT_HOST_BANK_ROOT"])
    (root / "source").symlink_to(outside, target_is_directory=True)
    semantic = root / "semantic"
    semantic.mkdir()
    (semantic / "annotations.json").write_text("{}")
    (semantic / "curriculum.json").write_text("{}")
    values.update(TEASSISTANT_BANK_DIR="/bank/source", TEASSISTANT_SEMANTIC_DIR="/bank/semantic")
    with pytest.raises(deploy.DeploymentError):
        deploy.validate_config(values)


def test_failed_compose_validation_prevents_mutating_command_and_redacts_output(
    tmp_path, monkeypatch, capsys
):
    path, _ = _config(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="SYNTHETIC_DSN_SECRET", stderr="secret")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    assert deploy.main(["--env-file", str(path), "up", "--build"]) == 2
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["config", "--quiet"]
    assert calls[0][1]["capture_output"] is True
    output = capsys.readouterr()
    assert "SYNTHETIC_DSN_SECRET" not in output.out + output.err


def test_admin_invocation_has_no_shell_or_password_argument(tmp_path, monkeypatch):
    path, _ = _config(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    name = "synthetic-admin; echo should-not-execute"
    assert deploy.main(["--env-file", str(path), "admin", "--username", name]) == 0
    command, kwargs = calls[-1]
    assert command[-4:] == ["--username", name, "--display-name", "管理员"]
    assert not kwargs.get("shell", False)
    assert not any("password" in arg.lower() for arg in command)


@pytest.mark.parametrize("provider,allow", [("codex", "0"), ("deepseek", "1"), ("other", "0")])
def test_unsafe_routing_never_executes_even_valid_argv(monkeypatch, capsys, provider, allow):
    monkeypatch.setenv("TEASSISTANT_MODEL_PROVIDER", provider)
    monkeypatch.setenv("TEASSISTANT_ALLOW_CODEX", allow)
    monkeypatch.setenv("TEASSISTANT_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SYNTHETIC_SECRET")
    executed = []
    monkeypatch.setattr(container_entrypoint.os, "execvpe", lambda *args: executed.append(args))
    assert container_entrypoint.main(["/app/.venv/bin/python", "safe.py"]) == 78
    assert not executed
    captured = capsys.readouterr()
    assert "SYNTHETIC_SECRET" not in captured.out + captured.err


def test_pg_health_bounds_connection_and_statement_and_disposes_on_error(monkeypatch, capsys):
    observed = {}

    class Engine:
        def connect(self):
            raise RuntimeError("postgresql://user:SYNTHETIC_SECRET@private/db")

        def dispose(self):
            observed["disposed"] = True

    def engine(url, **kwargs):
        observed.update(kwargs)
        return Engine()

    monkeypatch.setattr(container_healthcheck, "create_engine", engine)
    assert not container_healthcheck.database_ready("postgresql://user:synthetic@db/database")
    assert observed["connect_args"] == {
        "connect_timeout": 2,
        "options": "-c statement_timeout=2000",
    }
    assert observed["disposed"] is True
    captured = capsys.readouterr()
    assert "SYNTHETIC_SECRET" not in captured.out + captured.err


@pytest.mark.parametrize(
    "cmdline",
    [
        b"sh\0-c\0/app/.venv/bin/python /app/scripts/run_web_worker.py\0",
        b"/app/.venv/bin/python\0/app/scripts/run_web_worker.py.evil\0",
        b"/app/.venv/bin/python\0/app/scripts/run_web_worker.py\0--once\0",
        b"/app/.venv/bin/python\0/app/scripts/manage_web.py\0",
    ],
)
def test_worker_health_rejects_similar_or_wrapper_processes(cmdline):
    assert not container_healthcheck.worker_pid1_ready(cmdline)


def test_healthy_worker_requires_database_not_only_pid(monkeypatch):
    monkeypatch.setattr(container_healthcheck, "worker_pid1_ready", lambda: True)
    monkeypatch.setattr(container_healthcheck, "database_ready", lambda: False)
    assert not container_healthcheck.check_role("worker")
    assert not container_healthcheck.check_role("unknown")


def test_smoke_worker_overrides_images_api_healthcheck(tmp_path):
    smoke = _script("container_smoke")
    compose = smoke._compose_text("synthetic-image:local", tmp_path / "share", "a" * 32)
    worker = compose.split("  worker:\n", 1)[1].split("\nvolumes:", 1)[0]
    assert "healthcheck:" in worker
    assert "container_healthcheck.py" in worker
    assert re.search(r"--role[\s,\"']+worker", worker)


def _forbidden_context_paths(names):
    """Inspect every component, including nested private runtime directories."""
    forbidden = {
        "work",
        "outputs",
        "runtime",
        "bank",
        "assets",
        ".git",
        ".venv",
        ".cache",
        "node_modules",
        ".pnpm-store",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "secrets",
        "credentials",
    }
    leaks = []
    for name in names:
        parts = PurePosixPath(name).parts
        if parts and parts[0] == "context":
            parts = parts[1:]
        pairs = set(zip(parts, parts[1:]))
        if (
            forbidden.intersection(parts)
            or any(part.startswith(".env") for part in parts)
            or ("evals", "private") in pairs
            or ("frontend", "dist") in pairs
            or PurePosixPath(name).suffix in {".pem", ".key", ".p12", ".pfx"}
        ):
            leaks.append(name)
    return leaks


def test_context_auditor_rejects_private_components_at_any_depth():
    denied = [
        "context/backend/work/lo-profile/registrymodifications.xcu",
        "context/scripts/nested/outputs/source.xml",
        "context/backend/.git/config",
        "context/backend/.venv/pyvenv.cfg",
        "context/frontend/.cache/index",
        "context/backend/evals/private/source.xml",
        "context/backend/runtime/bank/source.xml",
        "context/frontend/node_modules/dependency/index.js",
    ]
    assert _forbidden_context_paths(denied) == denied
    assert not _forbidden_context_paths(
        ["context/backend/app/web_jobs.py", "context/templates/exam-print-v1/teacher.docx"]
    )


def test_dockerignore_explicitly_excludes_nested_private_directories():
    patterns = set((Path(__file__).resolve().parents[2] / ".dockerignore").read_text().splitlines())
    required = {"**/work", "**/outputs", "**/.git", "**/.venv", "**/.cache", "**/evals/private"}
    assert required <= patterns
