import base64
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from app.ai import AIResponseError, DeepSeekConfig
from app.codex_cli import CodexCLIConfig
from app.semantic_models import (
    CodexSemanticAdapter,
    DeepSeekSemanticAdapter,
    SemanticResponseError,
    strict_semantic_batch_schema,
)


def batch_payload():
    return {
        "assessments": [
            {
                "question_id": "q1",
                "status": "assessed",
                "point_ids": ["p1"],
                "prerequisite_ids": [],
                "difficulty_score": 51,
                "reasoning": "合成样例需要两步推理。",
                "point_evidence": [
                    {"point_id": "p1", "role": "target", "reason": "出现比例关系。"}
                ],
                "warnings": [],
            }
        ]
    }


def png_bytes():
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK9sAAAAASUVORK5CYII="
    )


def object_schemas(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from object_schemas(value)
    elif isinstance(node, list):
        for value in node:
            yield from object_schemas(value)


def test_strict_schema_inlines_refs_and_requires_all_properties():
    schema = strict_semantic_batch_schema()
    serialized = json.dumps(schema)
    assert "$ref" not in serialized and "$defs" not in serialized
    for node in object_schemas(schema):
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"])


@pytest.mark.asyncio
async def test_codex_uses_ephemeral_read_only_stdin_schema_and_multiple_temp_images(
    monkeypatch, tmp_path
):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(png_bytes())
    second.write_bytes(png_bytes())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-codex")
    captured = {}

    def fake_popen(argv, **kwargs):
        class FakeProcess:
            pid = 92531
            returncode = 0

            def communicate(self, input=None, timeout=None):
                captured["argv"] = argv
                captured["kwargs"] = kwargs
                captured["input"] = input
                result = Path(argv[argv.index("--output-last-message") + 1])
                result.write_text(json.dumps(batch_payload()), encoding="utf-8")
                schema_path = Path(argv[argv.index("--output-schema") + 1])
                captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
                image_paths = [
                    Path(argv[index + 1]) for index, arg in enumerate(argv[:-1]) if arg == "--image"
                ]
                captured["images"] = [path.read_bytes() for path in image_paths]
                captured["image_paths"] = image_paths
                return "ignored stdout", "ignored private stderr"

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = await CodexSemanticAdapter(CodexCLIConfig(model=None)).assess(
        prompt="Rate the synthetic question.", image_paths=[first, second]
    )

    argv = captured["argv"]
    assert "--ephemeral" in argv and "--ignore-user-config" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[-1] == "-"
    assert argv.count("--image") == 2
    assert captured["images"] == [png_bytes(), png_bytes()]
    assert captured["kwargs"]["env"].get("DEEPSEEK_API_KEY") is None
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == Path(argv[argv.index("--cd") + 1])
    assert "Rate the synthetic question." in captured["input"]
    assert result.provider == "codex_cli"
    assert result.model == "cli-default"
    assert result.model_metadata_source == "cli-default-unresolved"
    assert result.attempts == 1 and result.usage is None
    assert result.raw_json_result == batch_payload()
    assert not Path(captured["kwargs"]["cwd"]).exists()


@pytest.mark.asyncio
async def test_codex_rejects_invalid_schema_output_without_exposing_cli_output(monkeypatch):
    def fake_popen(argv, **kwargs):
        class FakeProcess:
            pid = 92532
            returncode = 0

            def communicate(self, input=None, timeout=None):
                Path(argv[argv.index("--output-last-message") + 1]).write_text(
                    "{}", encoding="utf-8"
                )
                return "secret stdout", "secret stderr"

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(AIResponseError) as exc:
        await CodexSemanticAdapter(CodexCLIConfig()).assess(prompt="synthetic", image_paths=[])
    assert "secret" not in str(exc.value)
    assert isinstance(exc.value, SemanticResponseError)
    assert exc.value.error_code == "schema_validation"
    assert exc.value.raw_response == "{}"
    assert exc.value.raw_json_result == {}
    assert exc.value.attempts == 1 and exc.value.usage is None


@pytest.mark.asyncio
async def test_codex_timeout_kills_process_group(monkeypatch):
    killed = []

    class FakeProcess:
        pid = 92533
        returncode = None

        def communicate(self, input=None, timeout=None):
            assert timeout == 0.25
            raise subprocess.TimeoutExpired("codex", timeout)

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        CodexSemanticAdapter,
        "_kill_process_tree",
        staticmethod(lambda process: killed.append(process.pid)),
    )
    adapter = CodexSemanticAdapter(CodexCLIConfig(timeout_seconds=0.25))
    with pytest.raises(AIResponseError, match="timed out"):
        await adapter.assess(prompt="synthetic", image_paths=[])
    assert killed == [92533]


@pytest.mark.asyncio
async def test_deepseek_posts_json_batch_with_images_and_validates_response(tmp_path):
    image = tmp_path / "input.png"
    image.write_bytes(png_bytes())
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(batch_payload(), ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 23, "completion_tokens": 17, "total_tokens": 40},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    adapter = DeepSeekSemanticAdapter(DeepSeekConfig(api_key="unit-test-key"), client=client)
    try:
        result = await adapter.assess(
            prompt="Assess only this synthetic question.", image_paths=[image]
        )
    finally:
        await client.aclose()
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer unit-test-key"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 8192
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert result.provider == "deepseek" and result.model_metadata_source == "configured_model"
    assert result.attempts == 1 and result.usage == {
        "prompt_tokens": 23,
        "completion_tokens": 17,
        "total_tokens": 40,
    }
    assert result.raw_json_result == batch_payload()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish,content",
    [("length", json.dumps(batch_payload())), ("stop", "   "), ("content_filter", "{}")],
)
async def test_deepseek_rejects_truncated_or_empty_batch(finish, content):
    def handler(_request):
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": finish, "message": {"content": content}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    adapter = DeepSeekSemanticAdapter(DeepSeekConfig(api_key="unit-test-key"), client=client)
    try:
        with pytest.raises(AIResponseError) as exc:
            await adapter.assess(prompt="synthetic", image_paths=[])
    finally:
        await client.aclose()
    assert isinstance(exc.value, SemanticResponseError)
    assert exc.value.attempts == 1


@pytest.mark.asyncio
async def test_deepseek_preserves_invalid_json_and_finish_reason_metadata():
    cases = [
        ("stop", "{not-json", "invalid_json", None),
        ("stop", '{"assessments":[]}', "schema_validation", {"assessments": []}),
        ("length", json.dumps(batch_payload()), "finish_reason", batch_payload()),
    ]
    for finish, content, code, expected_json in cases:

        def handler(_request):
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": finish, "message": {"content": content}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        adapter = DeepSeekSemanticAdapter(DeepSeekConfig(api_key="unit-test-key"), client=client)
        try:
            with pytest.raises(SemanticResponseError) as exc:
                await adapter.assess(prompt="synthetic", image_paths=[])
        finally:
            await client.aclose()
        assert str(exc.value) in {
            "model response did not match the semantic batch contract",
            "model response did not finish normally",
        }
        assert exc.value.error_code == code
        assert exc.value.raw_response == content
        assert exc.value.raw_json_result == expected_json
        assert exc.value.attempts == 1
        assert exc.value.usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
        assert "synthetic" not in repr(exc.value)


@pytest.mark.asyncio
async def test_deepseek_transport_failures_keep_bounded_attempt_metadata():
    for status, expected_attempts, expected_code in [
        (429, 2, "provider_unavailable"),
        (400, 1, "provider_permanent"),
    ]:
        calls = 0

        def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(status, json={"error": "private provider payload"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        config = DeepSeekConfig(api_key="unit-test-key", max_attempts=2, retry_backoff_seconds=0)
        adapter = DeepSeekSemanticAdapter(config, client=client)
        try:
            with pytest.raises(SemanticResponseError) as exc:
                await adapter.assess(prompt="synthetic", image_paths=[])
        finally:
            await client.aclose()
        assert exc.value.error_code == expected_code
        assert exc.value.attempts == expected_attempts == calls
        assert exc.value.raw_response is None and exc.value.raw_json_result is None
        assert exc.value.usage is None
        assert "private provider payload" not in str(exc.value)
