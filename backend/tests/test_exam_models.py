from __future__ import annotations

import asyncio
import json
import stat
import textwrap
from pathlib import Path

import httpx
import pytest
from app.ai import AIConfigurationError, DeepSeekConfig
from app.codex_cli import CodexCLIConfig
from app.exam_models import (
    CodexExamAdapter,
    DeepSeekExamAdapter,
    ExamModelResponseError,
    _parse_response,
    strict_exam_response_schema,
)
from PIL import Image


def _write_png(path: Path) -> bytes:
    Image.new("RGB", (24, 16), "white").save(path, format="PNG")
    return path.read_bytes()


def _deepseek_config(**changes) -> DeepSeekConfig:
    values = {
        "api_key": "synthetic-test-key",
        "max_attempts": 3,
        "retry_backoff_seconds": 0,
    }
    values.update(changes)
    return DeepSeekConfig(**values)


def test_task_specific_schema_has_only_allowed_stage_properties() -> None:
    paper = strict_exam_response_schema("paper")
    response = strict_exam_response_schema("response")
    reference = strict_exam_response_schema("reference")

    assert paper["properties"]["task"]["enum"] == ["paper"]
    assert "grading" in paper["properties"] and "printed_page_number" in paper["properties"]
    assert "diagnoses" not in paper["properties"]
    assert "exam_title" in response["properties"]
    assert "questions" not in response["properties"]
    assert "reference_answers" in reference["properties"]
    assert "responses" not in reference["properties"]
    assert paper["additionalProperties"] is False


def test_parse_response_rejects_wrong_task_stage_fields_and_invalid_evidence() -> None:
    for raw, code in (
        ('{"task":"response","responses":[]}', "task_mismatch"),
        ('{"task":"layout","diagnoses":[]}', "stage_field_mismatch"),
        (
            '{"task":"layout","regions":[{"page_id":"p","bbox":[0,0,2,1],"role":"paper"}]}',
            "schema_validation",
        ),
        ("not json", "invalid_json"),
    ):
        with pytest.raises(ExamModelResponseError) as caught:
            _parse_response(raw, expected_task="layout", attempts=1, usage=None)
        assert caught.value.error_code == code
        assert "not json" not in str(caught.value)


def test_deepseek_adapter_sends_json_schema_and_all_images_and_returns_validated_result(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "page.png"
    image_bytes = _write_png(image_path)
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 8192
        system_content = body["messages"][0]["content"]
        assert '"enum":["layout"]' in system_content
        assert "reference_answers" not in system_content
        image_url = body["messages"][1]["content"][1]["image_url"]["url"]
        assert image_url.endswith(__import__("base64").b64encode(image_bytes).decode())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"task": "layout", "page_classification": "response"}
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                    "private": 7,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = DeepSeekExamAdapter(_deepseek_config(), client=client)
    result = asyncio.run(
        adapter.assess(task="layout", prompt="classify the page", image_paths=[image_path])
    )
    asyncio.run(client.aclose())

    assert len(observed) == 1
    assert str(observed[0].url) == "https://api.deepseek.com/chat/completions"
    assert observed[0].headers["authorization"] == "Bearer synthetic-test-key"
    assert result.provider == "deepseek" and result.model == "deepseek-flash"
    assert result.attempts == 1
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
    assert result.raw_json_result["task"] == "layout"


def test_deepseek_retries_429_and_permanent_error_is_not_retried(tmp_path: Path) -> None:
    attempts = 0

    def retrying(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="synthetic provider body")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"task":"layout"}'}, "finish_reason": "stop"}]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(retrying))
    result = asyncio.run(
        DeepSeekExamAdapter(_deepseek_config(), client=client).assess(
            task="layout", prompt="test", image_paths=[]
        )
    )
    asyncio.run(client.aclose())
    assert attempts == 2 and result.attempts == 2

    permanent_attempts = 0

    def permanent(_request: httpx.Request) -> httpx.Response:
        nonlocal permanent_attempts
        permanent_attempts += 1
        return httpx.Response(400, text="private synthetic error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(permanent))
    with pytest.raises(ExamModelResponseError) as caught:
        asyncio.run(
            DeepSeekExamAdapter(_deepseek_config(), client=client).assess(
                task="layout", prompt="test", image_paths=[]
            )
        )
    asyncio.run(client.aclose())
    assert caught.value.error_code == "provider_permanent"
    assert permanent_attempts == 1
    assert "synthetic provider body" not in str(caught.value)
    assert "synthetic-test-key" not in str(caught.value)


def test_deepseek_rejects_truncated_json_even_if_it_parses() -> None:
    raw = {"choices": [{"message": {"content": '{"task":"layout"}'}, "finish_reason": "length"}]}

    with pytest.raises(ExamModelResponseError) as caught:
        DeepSeekExamAdapter._decode(raw, expected_task="layout", attempts=1, usage=None)

    assert caught.value.error_code == "finish_reason"
    assert caught.value.raw_json_result == {"task": "layout"}
    assert "task" not in str(caught.value)


def _write_fake_cli(path: Path, script: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(script), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_codex_requires_explicit_model_and_isolates_read_only_request(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _write_fake_cli(
        tmp_path / "fake-codex",
        """
        import json, os, pathlib, sys
        args = sys.argv[1:]
        required = ["--ephemeral", "--ignore-user-config", "--sandbox", "read-only", "--model", "synthetic-model"]
        assert all(value in args for value in required)
        schema = json.loads(pathlib.Path(args[args.index("--output-schema") + 1]).read_text())
        assert schema["properties"]["task"]["enum"] == ["layout"]
        result = pathlib.Path(args[args.index("--output-last-message") + 1])
        images = [pathlib.Path(args[i + 1]) for i, value in enumerate(args[:-1]) if value == "--image"]
        assert len(images) == 1 and images[0].is_file()
        prompt = sys.stdin.read()
        assert "untrusted source data" in prompt and "private prompt" in prompt
        assert "DEEPSEEK_API_KEY" not in os.environ
        result.write_text(json.dumps({"task": "layout", "page_classification": "blank"}))
        """,
    )
    image = tmp_path / "page.png"
    _write_png(image)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-never-forward")
    config = CodexCLIConfig(executable=str(fake), model="synthetic-model", timeout_seconds=5)
    adapter = CodexExamAdapter(config)

    result = asyncio.run(
        adapter.assess(task="layout", prompt="private prompt", image_paths=[image])
    )

    assert result.provider == "codex_cli"
    assert result.model == "synthetic-model"
    assert result.attempts == 1 and result.usage is None
    assert result.raw_json_result["page_classification"] == "blank"
    with pytest.raises(AIConfigurationError, match="CODEX_MODEL"):
        CodexExamAdapter(CodexCLIConfig(executable=str(fake), model=None))


def test_codex_preserves_invalid_response_and_safe_cli_diagnostics(tmp_path: Path) -> None:
    fake = _write_fake_cli(
        tmp_path / "bad-codex",
        """
        import pathlib, sys
        args = sys.argv[1:]
        pathlib.Path(args[args.index("--output-last-message") + 1]).write_text('{"task":"layout","diagnoses":[]}')
        sys.stderr.write('synthetic CLI diagnostic')
        sys.exit(3)
        """,
    )
    adapter = CodexExamAdapter(
        CodexCLIConfig(executable=str(fake), model="synthetic-model", timeout_seconds=5)
    )

    with pytest.raises(ExamModelResponseError) as caught:
        asyncio.run(adapter.assess(task="layout", prompt="test", image_paths=[]))

    assert caught.value.error_code == "cli_exit"
    assert caught.value.raw_json_result == {"task": "layout", "diagnoses": []}
    assert "synthetic CLI diagnostic" in (caught.value.diagnostics or "")
    assert "synthetic CLI diagnostic" not in str(caught.value)


def test_codex_timeout_kills_process_and_keeps_safe_error(tmp_path: Path) -> None:
    fake = _write_fake_cli(
        tmp_path / "slow-codex",
        """
        import time
        time.sleep(3)
        """,
    )
    adapter = CodexExamAdapter(
        CodexCLIConfig(executable=str(fake), model="synthetic-model", timeout_seconds=0.1)
    )

    with pytest.raises(ExamModelResponseError) as caught:
        asyncio.run(adapter.assess(task="layout", prompt="test", image_paths=[]))

    assert caught.value.error_code == "timeout"
    assert "timed out" in str(caught.value)


def test_codex_rejects_result_symlink_to_external_json(tmp_path: Path) -> None:
    external = tmp_path / "outside.json"
    external.write_text('{"task":"layout","page_classification":"blank"}', encoding="utf-8")
    fake = _write_fake_cli(
        tmp_path / "symlink-codex",
        f"""
        import os, pathlib, sys
        args = sys.argv[1:]
        result = pathlib.Path(args[args.index("--output-last-message") + 1])
        os.symlink({str(external)!r}, result)
        """,
    )
    adapter = CodexExamAdapter(
        CodexCLIConfig(executable=str(fake), model="synthetic-model", timeout_seconds=5)
    )

    with pytest.raises(ExamModelResponseError) as caught:
        asyncio.run(adapter.assess(task="layout", prompt="test", image_paths=[]))

    assert caught.value.error_code == "missing_output"
    assert caught.value.raw_json_result is None
    assert str(external) not in str(caught.value)
