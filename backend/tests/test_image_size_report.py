"""Image accounting must not expose environment variables or confuse disk with RAM."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "image_size_report", Path(__file__).resolve().parents[2] / "scripts/image_size_report.py"
)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


def test_report_limits_inspection_to_size_and_platform(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout='{"platform":"linux/arm64","size_bytes":1073741824}')

    monkeypatch.setattr(reporter.subprocess, "run", run)
    report = reporter.image_size_report("teassistant:synthetic")
    command, options = calls[0]
    assert command[:4] == ["docker", "image", "inspect", "--format"]
    assert ".Env" not in command[4]
    assert command[-1] == "teassistant:synthetic"
    assert options["timeout"] == 30
    assert "linux/arm64" in report and "1024.0 MiB" in report
    assert "not registry download size" in report


@pytest.mark.parametrize("image", ["", "--help", "one two", "image\nother"])
def test_rejects_invalid_image_before_docker_call(image, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Docker must not be called")

    monkeypatch.setattr(reporter.subprocess, "run", unexpected)
    with pytest.raises(ValueError):
        reporter.image_size_report(image)


@pytest.mark.parametrize("size", [-1, True, "1024"])
def test_invalid_size_is_not_published(size, monkeypatch):
    import json

    monkeypatch.setattr(
        reporter.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"platform": "linux/arm64", "size_bytes": size})
        ),
    )
    with pytest.raises(ValueError):
        reporter.image_size_report("teassistant:synthetic")


@pytest.mark.parametrize("size,passes", [(199999999, True), (200000000, True), (200000001, False)])
def test_decimal_200mb_limit_is_enforced(size, passes, monkeypatch):
    import json

    monkeypatch.setattr(
        reporter.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"platform": "linux/arm64", "size_bytes": size})
        ),
    )
    if passes:
        assert "linux/arm64" in reporter.image_size_report(
            "teassistant:synthetic", max_bytes=200000000
        )
    else:
        with pytest.raises(ValueError, match="exceeds limit"):
            reporter.image_size_report("teassistant:synthetic", max_bytes=200000000)
