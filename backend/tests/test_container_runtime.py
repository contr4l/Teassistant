from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _script(name: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


container_entrypoint = _script("container_entrypoint")
container_healthcheck = _script("container_healthcheck")


def _production_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEASSISTANT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("TEASSISTANT_ALLOW_CODEX", "0")
    monkeypatch.setenv("TEASSISTANT_DATABASE_URL", "sqlite:///:memory:")
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir(exist_ok=True)
    curriculum = tmp_path / "curriculum.json"
    curriculum.write_text("{}", encoding="utf-8")
    (tmp_path / "web").mkdir(exist_ok=True)
    monkeypatch.setenv("TEASSISTANT_FRONTEND_DIST", str(frontend_dist))
    monkeypatch.setenv("TEASSISTANT_CURRICULUM", str(curriculum))
    monkeypatch.setenv("TEASSISTANT_DATA_DIR", str(tmp_path / "web" / "private"))


def test_entrypoint_config_check_never_echoes_key(monkeypatch, capsys, tmp_path) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret-must-not-print")

    assert container_entrypoint.main(["--check-config"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "container configuration valid\n"
    assert "synthetic-secret-must-not-print" not in captured.out + captured.err


def test_container_does_not_silently_use_developer_sqlite_default(monkeypatch, tmp_path):
    _production_env(monkeypatch, tmp_path)
    monkeypatch.delenv("TEASSISTANT_DATABASE_URL")
    assert container_entrypoint.main(["--check-config"]) == 78


def test_remote_tools_reject_private_container_temporary_directory(monkeypatch, tmp_path):
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TEASSISTANT_TOOLS_URL", "http://tools:8001")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "not-shared"))
    assert container_entrypoint.main(["--check-config"]) == 78
    assert not (tmp_path / "not-shared").exists()


def test_entrypoint_rejects_codex_routing_without_echoing_configuration(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("TEASSISTANT_MODEL_PROVIDER", "codex")
    monkeypatch.setenv("TEASSISTANT_ALLOW_CODEX", "1")
    monkeypatch.setenv("TEASSISTANT_DATABASE_URL", "postgresql://teacher:private@db/teassistant")

    assert container_entrypoint.main(["--check-config"]) == 78

    captured = capsys.readouterr()
    assert captured.err == "container configuration invalid\n"
    assert "private" not in captured.out + captured.err
    assert "teassistant" not in captured.out + captured.err


def test_entrypoint_execs_argument_vector_without_shell(monkeypatch, tmp_path) -> None:
    _production_env(monkeypatch, tmp_path)
    observed = {}

    def fake_exec(path, argv, env):
        observed.update(path=path, argv=argv, env=env)

    monkeypatch.setattr(container_entrypoint.os, "execvpe", fake_exec)
    command = ["/app/.venv/bin/python", "-c", "print('arg with spaces')"]

    assert container_entrypoint.main(command) == 0
    assert observed["path"] == command[0]
    assert observed["argv"] == command
    assert observed["env"]["TEASSISTANT_MODEL_PROVIDER"] == "deepseek"


def test_entrypoint_requires_a_command_after_config_validation(
    monkeypatch, capsys, tmp_path
) -> None:
    _production_env(monkeypatch, tmp_path)

    assert container_entrypoint.main([]) == 64

    assert capsys.readouterr().err == "container command required\n"


def test_api_health_requires_http_and_database(monkeypatch) -> None:
    checks = {"http": False, "db": True}
    monkeypatch.setattr(container_healthcheck, "api_http_ready", lambda: checks["http"])
    monkeypatch.setattr(container_healthcheck, "database_ready", lambda: checks["db"])

    assert not container_healthcheck.check_role("api")
    checks["http"] = True
    assert container_healthcheck.check_role("api")
    checks["db"] = False
    assert not container_healthcheck.check_role("api")


def test_api_http_probe_requires_200_and_uses_fixed_timeout(monkeypatch) -> None:
    observed = {}

    class FakeConnection:
        def __init__(self, host, port, timeout):
            observed.update(host=host, port=port, timeout=timeout)

        def request(self, method, path):
            observed.update(method=method, path=path)

        def getresponse(self):
            return SimpleNamespace(status=200)

        def close(self):
            observed["closed"] = True

    monkeypatch.setattr(container_healthcheck.http.client, "HTTPConnection", FakeConnection)

    assert container_healthcheck.api_http_ready()
    assert observed == {
        "host": "127.0.0.1",
        "port": 8000,
        "timeout": 2,
        "method": "GET",
        "path": "/",
        "closed": True,
    }


def test_worker_health_requires_exact_pid1_script_and_database(monkeypatch) -> None:
    expected = b"/app/.venv/bin/python\0/app/scripts/run_web_worker.py\0"
    assert container_healthcheck.worker_pid1_ready(expected)
    assert not container_healthcheck.worker_pid1_ready(
        b"/usr/bin/python3\0/app/scripts/run_web_worker.py\0"
    )
    assert not container_healthcheck.worker_pid1_ready(expected + b"--once\0")

    monkeypatch.setattr(container_healthcheck, "worker_pid1_ready", lambda: True)
    monkeypatch.setattr(container_healthcheck, "database_ready", lambda: False)
    assert not container_healthcheck.check_role("worker")


def test_database_health_runs_bounded_select_without_database_server(monkeypatch, tmp_path) -> None:
    _production_env(monkeypatch, tmp_path)

    assert container_healthcheck.database_ready()


def test_entrypoint_path_validation_fails_closed_with_generic_error(
    monkeypatch, capsys, tmp_path
) -> None:
    _production_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TEASSISTANT_FRONTEND_DIST", str(tmp_path / "missing-dist"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret-must-not-print")

    assert container_entrypoint.main(["--check-config"]) == 78

    captured = capsys.readouterr()
    assert captured.err == "container configuration invalid\n"
    assert "synthetic-secret-must-not-print" not in captured.out + captured.err
