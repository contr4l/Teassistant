"""Configuration uses synthetic secrets; no Docker daemon or model is called."""

import importlib.util
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "deployment_cli", Path(__file__).resolve().parents[2] / "scripts/deploy.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def config(tmp_path):
    path = tmp_path / "deployment.env"
    deploy.initialize(path)
    return path, deploy.read_config(path)


def test_init_creates_private_random_config_and_refuses_existing_file(tmp_path):
    path, values = config(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(values["TEASSISTANT_DB_PASSWORD"]) == 64
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        deploy.initialize(path)
    assert path.read_bytes() == original
    other, second = config(tmp_path / "second")
    assert other.is_file()
    assert values["TEASSISTANT_DB_PASSWORD"] != second["TEASSISTANT_DB_PASSWORD"]
    assert len(deploy.validate_config(values)) == 2  # Explicit bank/model not ready warnings.


def test_unsupported_dsn_password_fails_without_printing_synthetic_secret(tmp_path, capsys):
    path, _values = config(tmp_path)
    secret = "synthetic-secret-with-@-and-:"
    path.write_text(
        path.read_text().replace("TEASSISTANT_DB_PASSWORD=", "TEASSISTANT_DB_PASSWORD=" + secret)
    )
    assert deploy.main(["--env-file", str(path), "check"]) == 2
    assert secret not in capsys.readouterr().err


def test_configured_bank_requires_real_pair_and_container_mount_paths(tmp_path):
    _path, values = config(tmp_path)
    values["TEASSISTANT_BANK_DIR"] = "/bank/source"
    with pytest.raises(deploy.DeploymentError, match="both"):
        deploy.validate_config(values)
    values["TEASSISTANT_SEMANTIC_DIR"] = "/bank/../outside"
    with pytest.raises(deploy.DeploymentError):
        deploy.validate_config(values)
    root = Path(values["TEASSISTANT_HOST_BANK_ROOT"])
    (root / "source").mkdir()
    (root / "semantic").mkdir()
    (root / "source/bank.json").write_text("{}")
    (root / "semantic/annotations.json").write_text("{}")
    values["TEASSISTANT_SEMANTIC_DIR"] = "/bank/semantic"
    with pytest.raises(deploy.DeploymentError, match="curriculum"):
        deploy.validate_config(values)
    (root / "semantic/curriculum.json").write_text("{}")
    assert len(deploy.validate_config(values)) == 1


def test_compose_syntax_failure_does_not_relay_resolved_environment(tmp_path, monkeypatch, capsys):
    path, _values = config(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="synthetic-secret", stderr="synthetic-secret")

    monkeypatch.setattr(deploy.subprocess, "run", run)
    assert deploy.main(["--env-file", str(path), "check"]) == 2
    assert calls[0][-2:] == ["config", "--quiet"]
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.out + captured.err


def test_shell_overrides_follow_compose_precedence(tmp_path, monkeypatch):
    path, _values = config(tmp_path)
    monkeypatch.setenv("TEASSISTANT_PORT", "65536")
    with pytest.raises(deploy.DeploymentError, match="65535"):
        deploy.validate_config(deploy.read_config(path))


def test_stop_can_recover_when_configured_mount_has_disappeared(tmp_path, monkeypatch):
    path, values = config(tmp_path)
    Path(values["TEASSISTANT_HOST_BANK_ROOT"]).rmdir()
    calls = []
    monkeypatch.setattr(deploy.subprocess, "run", lambda command, **kwargs: calls.append(command))
    assert deploy.main(["--env-file", str(path), "stop"]) == 0
    assert len(calls) == 1 and calls[0][-1] == "stop"
