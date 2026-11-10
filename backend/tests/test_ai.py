import json

import httpx
import pytest
from app.ai import (
    AIConfigurationError,
    AIPermanentError,
    AIResponseError,
    DeepSeekAdapter,
    DeepSeekConfig,
    QuestionExtraction,
    question_from_extraction,
)


def question_payload(**changes):
    question = {
        "stem": [{"kind": "text", "text": "求 1+1"}],
        "answer": [{"kind": "text", "text": "2"}],
        "point_ids": ["p1"],
        "prerequisite_ids": [],
        "difficulty_score": 10,
        "bbox": [0.1, 0.1, 0.9, 0.9],
    }
    question.update(changes)
    return question


def completion(payload=None):
    return {
        "choices": [
            {
                "message": {"content": json.dumps(payload or question_payload())},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def make_adapter(handler, *, attempts=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    config = DeepSeekConfig(
        api_key="unit-test-secret",
        model="test-model",
        max_attempts=attempts,
        retry_backoff_seconds=0,
    )
    return DeepSeekAdapter(config, client=client), client


@pytest.mark.asyncio
async def test_success_includes_contract_allowlist_image_and_safe_metadata():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion())

    adapter, client = make_adapter(handler)
    try:
        result = await adapter.extract(
            prompt="Extract one synthetic item",
            allowed_point_ids={"p1"},
            question_id="q1",
            canonical_id="canon-q1",
            curriculum_version="hn-v1",
            asset_id="synthetic-asset",
            image_data_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK9sAAAAASUVORK5CYII=",
        )
    finally:
        await client.aclose()
    body = json.loads(seen[0].content)
    system_text = body["messages"][0]["content"]
    assert '"bbox"' in system_text and '"p1"' in system_text
    assert "canonical_id" not in system_text and "curriculum_version" not in system_text
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 8192
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png")
    assert str(seen[0].url) == "https://api.deepseek.com/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer unit-test-secret"
    assert result.provider == "deepseek" and result.model == "test-model"
    assert result.model_metadata_source == "configured_model"
    assert result.attempts == 1 and result.usage["total_tokens"] == 20
    assert result.question.id == "q1"
    assert result.question.model_version == "test-model"
    assert "id" not in result.raw_json_result
    assert "unit-test-secret" not in repr(adapter.config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("private timeout detail"), httpx.ConnectError("private network detail")],
)
async def test_timeout_and_network_errors_retry_then_succeed(failure):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure
        return httpx.Response(200, json=completion())

    adapter, client = make_adapter(handler)
    try:
        result = await adapter.extract(
            prompt="x",
            allowed_point_ids={"p1"},
            question_id="q",
            canonical_id="c",
            curriculum_version="v",
            asset_id="a",
        )
    finally:
        await client.aclose()
    assert calls == 2 and result.attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limit_and_service_failure_retry(status):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, text="provider secret response must stay hidden")
        return httpx.Response(200, json=completion())

    adapter, client = make_adapter(handler)
    try:
        result = await adapter.extract(
            prompt="x",
            allowed_point_ids={"p1"},
            question_id="q",
            canonical_id="c",
            curriculum_version="v",
            asset_id="a",
        )
    finally:
        await client.aclose()
    assert result.attempts == 2 and calls == 2


@pytest.mark.asyncio
async def test_permanent_client_error_does_not_retry_or_expose_response_or_key():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="provider token leaked in body")

    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(AIPermanentError) as exc:
            await adapter.extract(
                prompt="x",
                allowed_point_ids={"p1"},
                question_id="q",
                canonical_id="c",
                curriculum_version="v",
                asset_id="a",
            )
    finally:
        await client.aclose()
    assert calls == 1
    assert "provider token" not in str(exc.value)
    assert "unit-test-secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_curriculum_id_and_invalid_contract_are_rejected():
    async def run(payload):
        adapter, client = make_adapter(lambda _: httpx.Response(200, json=completion(payload)))
        try:
            return await adapter.extract(
                prompt="x",
                allowed_point_ids={"p1"},
                question_id="q",
                canonical_id="c",
                curriculum_version="v",
                asset_id="a",
            )
        finally:
            await client.aclose()

    with pytest.raises(AIResponseError, match="unknown curriculum"):
        await run(question_payload(point_ids=["surprise"]))
    with pytest.raises(AIResponseError, match="did not match"):
        await run(question_payload(answer=[]))


def test_base_url_and_image_url_are_strictly_validated():
    with pytest.raises(AIConfigurationError, match="fixed DeepSeek"):
        DeepSeekConfig(api_key="secret", model="x", base_url="https://evil.example")
    with pytest.raises(AIConfigurationError, match="fixed DeepSeek"):
        DeepSeekConfig(api_key="secret", model="x", base_url="http://api.deepseek.com")
    for invalid in (
        "https://api.deepseek.com/v1",
        "https://user:pass@api.deepseek.com",
        "https://api.deepseek.com:443",
    ):
        with pytest.raises(AIConfigurationError, match="fixed DeepSeek"):
            DeepSeekConfig(api_key="secret", model="x", base_url=invalid)


def test_deepseek_environment_defaults_and_ignores_legacy_bailian_variables(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_BASE_URL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_MODEL",
        "DASHSCOPE_BASE_URL",
        "BAILIAN_API_KEY",
        "BAILIAN_MODEL",
        "BAILIAN_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-secret")
    monkeypatch.setenv("BAILIAN_API_KEY", "legacy-secret")
    with pytest.raises(AIConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekConfig.from_env()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    config = DeepSeekConfig.from_env()
    assert config.model == "deepseek-flash"
    assert config.base_url == "https://api.deepseek.com"
    assert "deepseek-secret" not in repr(config)

    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-custom")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/")
    custom = DeepSeekConfig.from_env()
    assert custom.model == "deepseek-custom"
    assert custom.max_tokens == 8192


@pytest.mark.asyncio
async def test_configured_max_tokens_is_used_in_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = DeepSeekAdapter(DeepSeekConfig(api_key="test", max_tokens=2048), client=client)
    try:
        await adapter.extract(
            prompt="x",
            allowed_point_ids={"p1"},
            question_id="q",
            canonical_id="c",
            curriculum_version="v",
            asset_id="a",
        )
    finally:
        await client.aclose()
    assert json.loads(seen[0].content)["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_blank_json_content_and_non_stop_finish_reasons_fail_closed():
    async def run(content, finish_reason):
        envelope = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
        adapter, client = make_adapter(lambda _: httpx.Response(200, json=envelope))
        try:
            return await adapter.extract(
                prompt="x",
                allowed_point_ids={"p1"},
                question_id="q",
                canonical_id="c",
                curriculum_version="v",
                asset_id="a",
            )
        finally:
            await client.aclose()

    with pytest.raises(AIResponseError):
        await run("   \n  ", "stop")
    with pytest.raises(AIResponseError, match="finish normally"):
        await run(json.dumps(question_payload()), "length")
    for finish_reason in ("abort", "content_filter", "system_resource", "tool_calls"):
        with pytest.raises(AIResponseError, match="finish normally"):
            await run(json.dumps(question_payload()), finish_reason)


def test_public_extraction_helper_reuses_contract_white_list_and_metadata():
    extraction = QuestionExtraction.model_validate(question_payload())
    question = question_from_extraction(
        extraction,
        allowed_point_ids={"p1"},
        question_id="q1",
        canonical_id="c1",
        curriculum_version="hn-v1",
        asset_id="asset-1",
        page=2,
        model_version="offline-check",
    )
    assert question.id == "q1"
    assert question.sources[0].page == 2
    assert question.model_version == "offline-check"
    with pytest.raises(AIResponseError, match="unknown curriculum"):
        question_from_extraction(
            extraction,
            allowed_point_ids=set(),
            question_id="q1",
            canonical_id="c1",
            curriculum_version="hn-v1",
            asset_id="asset-1",
            model_version="offline-check",
        )
