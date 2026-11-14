from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("lean_deploy", ROOT / "scripts" / "deploy.py")
assert SPEC and SPEC.loader
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def _config(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    env_file = tmp_path / "deployment.env"
    deploy.initialize(env_file)
    return env_file, deploy.read_config(env_file)


def test_new_template_defaults_to_distinct_api_image_and_api_target(tmp_path):
    _env_file, values = _config(tmp_path)
    assert values["TEASSISTANT_API_IMAGE"] == "teassistant:api-local"
    assert values["TEASSISTANT_API_TARGET"] == "api"
    assert values["TEASSISTANT_API_IMAGE"] != values["TEASSISTANT_IMAGE"]
    assert deploy.validate_config(values)


def test_new_template_selects_business_worker_and_independent_tools(tmp_path):
    _env_file, values = _config(tmp_path)
    assert values["TEASSISTANT_WORKER_TARGET"] == "worker"
    assert values["TEASSISTANT_TOOLS_IMAGE"] not in {
        values["TEASSISTANT_IMAGE"], values["TEASSISTANT_API_IMAGE"]
    }


@pytest.mark.parametrize("target", ["api", "tools", "invalid"])
def test_invalid_business_worker_build_target_rejected(tmp_path, target):
    _env_file, values = _config(tmp_path)
    values["TEASSISTANT_WORKER_TARGET"] = target
    with pytest.raises(deploy.DeploymentError, match="Worker build target"):
        deploy.validate_config(values)


@pytest.mark.parametrize("application_key", ["TEASSISTANT_IMAGE", "TEASSISTANT_API_IMAGE"])
def test_tool_image_cannot_overwrite_application_image(tmp_path, application_key):
    _env_file, values = _config(tmp_path)
    values["TEASSISTANT_TOOLS_IMAGE"] = values[application_key]
    with pytest.raises(deploy.DeploymentError, match="distinct image"):
        deploy.validate_config(values)


@pytest.mark.parametrize("target", ["api-runtime", "worker", "invalid"])
def test_invalid_api_target_is_rejected(tmp_path, target):
    _env_file, values = _config(tmp_path)
    values["TEASSISTANT_API_TARGET"] = target
    with pytest.raises(deploy.DeploymentError, match="API build target"):
        deploy.validate_config(values)


def test_api_target_rejects_missing_or_same_image_reference(tmp_path):
    _env_file, values = _config(tmp_path)
    values.pop("TEASSISTANT_API_IMAGE")
    with pytest.raises(deploy.DeploymentError, match="distinct image"):
        deploy.validate_config(values)

    values["TEASSISTANT_API_IMAGE"] = values["TEASSISTANT_IMAGE"]
    with pytest.raises(deploy.DeploymentError, match="distinct image"):
        deploy.validate_config(values)


def test_api_target_rejects_worker_image_that_compose_defaults_when_empty(tmp_path):
    _env_file, values = _config(tmp_path)
    values["TEASSISTANT_IMAGE"] = ""
    values["TEASSISTANT_API_IMAGE"] = "teassistant:local"
    values["TEASSISTANT_API_TARGET"] = "api"
    with pytest.raises(deploy.DeploymentError, match="distinct image"):
        deploy.validate_config(values)


def test_legacy_configuration_without_api_keys_remains_valid(tmp_path):
    env_file, values = _config(tmp_path)
    lines = [
        line
        for line in env_file.read_text().splitlines()
        if not line.startswith(("TEASSISTANT_API_IMAGE=", "TEASSISTANT_API_TARGET="))
    ]
    env_file.write_text("\n".join(lines) + "\n")
    legacy = deploy.read_config(env_file)
    assert "TEASSISTANT_API_IMAGE" not in legacy
    assert "TEASSISTANT_API_TARGET" not in legacy
    assert deploy.validate_config(legacy)


@pytest.mark.parametrize(
    "operation,expected",
    [
        ("build", ["build", "worker", "api"]),
        ("pull", ["pull", "postgres", "tools", "worker", "api"]),
        ("build-tools", ["build", "tools"]),
    ],
)
def test_build_and_pull_cover_both_images_and_database(tmp_path, monkeypatch, operation, expected):
    env_file, _values = _config(tmp_path)
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deploy.subprocess, "run", run)
    assert deploy.main(["--env-file", str(env_file), operation]) == 0
    assert commands[0][-2:] == ["config", "--quiet"]
    assert commands[1][-len(expected) :] == expected


def test_deployment_failure_does_not_echo_synthetic_database_secret(tmp_path, monkeypatch, capsys):
    env_file, values = _config(tmp_path)
    synthetic_secret = values["TEASSISTANT_DB_PASSWORD"]

    def run(command, **_kwargs):
        if command[-2:] == ["config", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise subprocess.CalledProcessError(1, command, stderr=synthetic_secret)

    monkeypatch.setattr(deploy.subprocess, "run", run)
    assert deploy.main(["--env-file", str(env_file), "pull"]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert synthetic_secret not in output
