import base64
import json
import subprocess
from pathlib import Path

import pytest
from app.ai import AIConfigurationError, AIResponseError, QuestionExtraction
from app.codex_cli import (
    CodexCLIAdapter,
    CodexCLIConfig,
    strict_question_extraction_schema,
)
from pydantic import ValidationError


def extraction_payload(**changes):
    payload = {
        "stem": [{"kind": "text", "text": "求 1+1"}],
        "answer": [{"kind": "text", "text": "2"}],
        "solution": [],
        "subquestions": [],
        "point_ids": ["p1"],
        "prerequisite_ids": [],
        "difficulty_score": 10,
        "bbox": [0.1, 0.1, 0.9, 0.9],
    }
    payload.update(changes)
    return payload


def sample_png_data_url():
    # Valid 1x1 PNG used only to exercise local data URL handling.
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK9sAAAAASUVORK5CYII="
    )
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii"), raw


def _object_schemas(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _object_schemas(value)
    elif isinstance(node, list):
        for value in node:
            yield from _object_schemas(value)


def test_schema_inlines_refs_and_recursively_requires_every_object_property():
    schema = strict_question_extraction_schema()
    serialized = json.dumps(schema)
    assert "$ref" not in serialized and "$defs" not in serialized
    assert '"default"' not in serialized and '"prefixItems"' not in serialized
    for object_schema in _object_schemas(schema):
        properties = object_schema.get("properties", {})
        assert object_schema["additionalProperties"] is False
        assert object_schema["required"] == list(properties)
    assert schema["properties"]["bbox"]["minItems"] == 4
    assert schema["properties"]["bbox"]["maxItems"] == 4
    node_schema = schema["properties"]["stem"]["items"]
    assert node_schema["required"] == ["kind", "text", "latex", "asset_path"]
    assert {"type": "null"} in node_schema["properties"]["text"]["anyOf"]


def test_config_reads_executable_and_optional_model(monkeypatch):
    monkeypatch.setenv("CODEX_EXECUTABLE", "/opt/codex")
    monkeypatch.setenv("CODEX_MODEL", "gpt-test")
    config = CodexCLIConfig.from_env()
    assert config.executable == "/opt/codex"
    assert config.model == "gpt-test"

    monkeypatch.setenv("CODEX_MODEL", "")
    assert CodexCLIConfig.from_env().model is None
    with pytest.raises(AIConfigurationError):
        CodexCLIConfig(executable=" ")


@pytest.mark.asyncio
async def test_extract_uses_stdin_schema_output_file_and_read_only_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-codex")
    seen = {}

    def fake_run(argv, **kwargs):
        class FakeProcess:
            pid = 123
            returncode = 0

            def communicate(self, input=None, timeout=None):
                seen["argv"] = argv
                seen["kwargs"] = kwargs
                seen["input"] = input
                schema_path = Path(argv[argv.index("--output-schema") + 1])
                result_path = Path(argv[argv.index("--output-last-message") + 1])
                seen["schema"] = json.loads(schema_path.read_text())
                result_path.write_text(json.dumps(extraction_payload()), encoding="utf-8")
                return "not the business payload", "private progress details"

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    result = await CodexCLIAdapter(CodexCLIConfig(executable="codex", model="gpt-test")).extract(
        prompt="Extract the synthetic question.",
        allowed_point_ids={"p1"},
        question_id="q1",
        canonical_id="canon-q1",
        curriculum_version="curriculum-v1",
        asset_id="synthetic-asset",
    )

    argv = seen["argv"]
    kwargs = seen["kwargs"]
    assert argv[0:2] == ["codex", "exec"]
    assert "--ephemeral" in argv and "--skip-git-repo-check" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == "gpt-test"
    assert argv[-1] == "-"
    assert "Extract the synthetic question." not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "--ignore-rules" not in argv
    assert "--ignore-user-config" in argv
    assert "must-not-reach-codex" not in kwargs["env"].get("DEEPSEEK_API_KEY", "")
    assert "Allowed curriculum point IDs" in seen["input"]
    assert kwargs["cwd"] == Path(argv[argv.index("--cd") + 1])
    assert not Path(kwargs["cwd"]).exists()  # temporary data is removed after completion
    assert result.provider == "codex_cli"
    assert result.model == "gpt-test"
    assert result.model_metadata_source == "configured_model"
    assert result.usage is None and result.attempts == 1
    assert result.question.model_version == "gpt-test"
    assert result.raw_json_result["point_ids"] == ["p1"]
    assert seen["schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_extract_attaches_validated_data_url_as_temp_image(monkeypatch):
    data_url, expected_bytes = sample_png_data_url()
    seen = {}

    def fake_run(argv, **kwargs):
        class FakeProcess:
            pid = 123
            returncode = 0

            def communicate(self, input=None, timeout=None):
                image_path = Path(argv[argv.index("--image") + 1])
                seen["image_bytes"] = image_path.read_bytes()
                seen["image_exists_during_call"] = image_path.is_file()
                result_path = Path(argv[argv.index("--output-last-message") + 1])
                result_path.write_text(json.dumps(extraction_payload()), encoding="utf-8")
                return "", ""

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    await CodexCLIAdapter(CodexCLIConfig()).extract(
        prompt="Extract one question.",
        allowed_point_ids={"p1"},
        question_id="q1",
        canonical_id="c1",
        curriculum_version="v1",
        asset_id="a1",
        image_data_url=data_url,
    )
    assert seen == {"image_bytes": expected_bytes, "image_exists_during_call": True}


@pytest.mark.asyncio
async def test_default_cli_model_is_reported_as_unresolved(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        class FakeProcess:
            pid = 123
            returncode = 0

            def communicate(self, input=None, timeout=None):
                seen.extend(argv)
                result_path = Path(argv[argv.index("--output-last-message") + 1])
                result_path.write_text(json.dumps(extraction_payload()), encoding="utf-8")
                return "", ""

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    result = await CodexCLIAdapter(CodexCLIConfig()).extract(
        prompt="extract",
        allowed_point_ids={"p1"},
        question_id="q1",
        canonical_id="c1",
        curriculum_version="v1",
        asset_id="a1",
    )
    assert result.model == "cli-default"
    assert result.model_metadata_source == "cli-default-unresolved"
    assert "--model" not in seen


@pytest.mark.asyncio
async def test_cli_failure_and_invalid_output_do_not_expose_process_streams(monkeypatch):
    def nonzero(argv, **kwargs):
        class FakeProcess:
            pid = 123
            returncode = 7

            def communicate(self, input=None, timeout=None):
                return "private stdout", "private stderr"

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", nonzero)
    adapter = CodexCLIAdapter(CodexCLIConfig())
    args = dict(
        prompt="extract",
        allowed_point_ids={"p1"},
        question_id="q1",
        canonical_id="c1",
        curriculum_version="v1",
        asset_id="a1",
    )
    with pytest.raises(AIResponseError, match="exit 7") as error:
        await adapter.extract(**args)
    assert "private" not in str(error.value)

    def invalid(argv, **kwargs):
        class FakeProcess:
            pid = 123
            returncode = 0

            def communicate(self, input=None, timeout=None):
                result_path = Path(argv[argv.index("--output-last-message") + 1])
                result_path.write_text("not-json", encoding="utf-8")
                return "private stdout", ""

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", invalid)
    with pytest.raises(AIResponseError, match="did not match") as error:
        await adapter.extract(**args)
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_unknown_curriculum_point_is_rejected(monkeypatch):
    def fake_run(argv, **kwargs):
        class FakeProcess:
            pid = 123
            returncode = 0

            def communicate(self, input=None, timeout=None):
                result_path = Path(argv[argv.index("--output-last-message") + 1])
                result_path.write_text(
                    json.dumps(extraction_payload(point_ids=["other"])), encoding="utf-8"
                )
                return "", ""

        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_run)
    with pytest.raises(AIResponseError, match="unknown curriculum"):
        await CodexCLIAdapter(CodexCLIConfig()).extract(
            prompt="extract",
            allowed_point_ids={"p1"},
            question_id="q1",
            canonical_id="c1",
            curriculum_version="v1",
            asset_id="a1",
        )


def test_shared_question_schema_still_validates_normalized_result():
    normalized = QuestionExtraction.model_validate(extraction_payload())
    assert normalized.point_ids == ["p1"]
    with pytest.raises(ValidationError):
        QuestionExtraction.model_validate(extraction_payload(bbox=[0, 0, 1]))
