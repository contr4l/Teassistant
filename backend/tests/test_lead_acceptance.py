"""Independent P0 acceptance: adversarial contract and allocation boundaries."""

import asyncio
import json
import sys
import time

import httpx
import pytest
from app.contracts import (
    Curriculum,
    KnowledgePoint,
    PracticeRequest,
    PracticeSnapshot,
    Question,
    Quota,
    StudentProgress,
)
from app.curriculum import CurriculumIndex
from app.evaluation import EvaluationManifest, PredictionSet, evaluate
from app.practice import build_practice, validate_current_progress
from pydantic import ValidationError


def curriculum():
    return Curriculum(version="synthetic-v1", points=[
        KnowledgePoint(id="pre", name="前置"),
        KnowledgePoint(id="a", name="A", prerequisite_ids=["pre"]),
        KnowledgePoint(id="b", name="B"),
    ])


def progress(points=None):
    return StudentProgress(student_id="student", revision="1",
                           curriculum_version="synthetic-v1",
                           learned_point_ids=["pre", "a", "b"] if points is None else points)


def question(qid, points, **updates):
    payload = dict(id=qid, canonical_id=qid, content_fingerprint=qid,
                   curriculum_version="synthetic-v1", stem=[dict(kind="text", text="题干")],
                   answer=[dict(kind="text", text="答案")], point_ids=points,
                   difficulty_score=30, sources=[dict(asset_id="synthetic", page=1,
                                                     bbox=[0, 0, 1, 1])])
    payload.update(updates)
    return Question.model_validate(payload)


def request(*quotas, **kwargs):
    return PracticeRequest(quotas=[Quota(point_id=p, count=n) for p, n in quotas], **kwargs)


def test_global_allocation_avoids_greedy_false_shortfall():
    result = build_practice([question("shared", ["a", "b"]), question("only-a", ["a"])],
                            curriculum(), progress(), request(("a", 1), ("b", 1)))
    assert not result.shortfalls
    assert {(i.target_point_id, i.question.id) for i in result.items} == {
        ("a", "only-a"), ("b", "shared")}


def test_transitive_identity_deduplication_and_original_exclusion():
    rows = [question("q1", ["a"], canonical_id="c1", content_fingerprint="f1"),
            question("q2", ["a"], canonical_id="c2", content_fingerprint="f2"),
            question("q3", ["a"], canonical_id="c1", content_fingerprint="f2")]
    result = build_practice(rows, curriculum(), progress(), request(("a", 3)))
    assert len(result.items) == 1
    assert [(s.requested, s.selected) for s in result.shortfalls] == [(3, 1)]
    excluded = build_practice(rows, curriculum(), progress(),
                              request(("a", 3), excluded_question_ids=["q2"]))
    assert not excluded.items


def test_shared_capacity_reports_actual_shortfall():
    result = build_practice([question("shared", ["a", "b"])], curriculum(), progress(),
                            request(("a", 1), ("b", 1)))
    assert len(result.items) == 1
    assert sum(s.requested - s.selected for s in result.shortfalls) == 1


def test_transitive_prerequisites_are_required_even_for_declared_learned_target():
    result = build_practice([question("q", ["a"])], curriculum(), progress(["a"]),
                            request(("a", 1)))
    assert not result.items


def test_additional_question_point_cannot_escape_progress_filter():
    result = build_practice([question("q", ["a", "b"])], curriculum(), progress(["a", "pre"]),
                            request(("a", 1)))
    assert not result.items


def test_unset_progress_is_rejected():
    with pytest.raises(ValueError):
        build_practice([question("q", ["a"])], curriculum(), progress([]), request(("a", 1)))


@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_advanced_quota_requires_strict_integers(value):
    with pytest.raises(ValidationError):
        Quota(point_id="a", count=1, by_difficulty={"basic": value})


def test_quota_and_difficulty_version_mismatch_rejected():
    with pytest.raises(ValidationError):
        Quota(point_id="a", count=2, by_difficulty={"basic": 1})
    with pytest.raises(ValueError):
        build_practice([question("q", ["a"], difficulty_version="other")],
                       curriculum(), progress(), request(("a", 1)))


def test_curriculum_cycle_and_unknown_prerequisite_rejected():
    for points in ([KnowledgePoint(id="a", name="A", prerequisite_ids=["missing"])],
                   [KnowledgePoint(id="a", name="A", prerequisite_ids=["b"]),
                    KnowledgePoint(id="b", name="B", prerequisite_ids=["a"])]):
        with pytest.raises(ValueError):
            CurriculumIndex(Curriculum(version="bad", points=points))


def test_snapshot_is_independent_of_input_mutation():
    row = question("q", ["a"])
    req = request(("a", 1))
    result = build_practice([row], curriculum(), progress(), req)
    row.stem[0].text = "变更题干"
    req.quotas[0].count = 2
    assert result.items[0].question.stem[0].text == "题干"
    assert result.request.quotas[0].count == 1


def test_export_boundary_rechecks_current_progress_and_student():
    result = build_practice([question("q", ["a"])], curriculum(), progress(), request(("a", 1)))
    for changed in (progress(["b"]), progress().model_copy(update={"student_id": "other"})):
        with pytest.raises(ValueError):
            validate_current_progress(result, curriculum(), changed)


def test_snapshot_rejects_overfill_and_unreported_shortfall():
    result = build_practice([question("q", ["a"])], curriculum(), progress(), request(("a", 1)))
    payload = result.model_dump()
    payload["request"]["quotas"][0]["count"] = 0
    with pytest.raises(ValidationError):
        PracticeSnapshot.model_validate(payload)
    payload = result.model_dump()
    payload["items"] = []
    with pytest.raises(ValidationError):
        PracticeSnapshot.model_validate(payload)


@pytest.mark.parametrize("exclusion", ["excluded_question_ids", "excluded_canonical_ids"])
def test_snapshot_rejects_selected_original_excluded_by_request(exclusion):
    result = build_practice([question("q", ["a"])], curriculum(), progress(), request(("a", 1)))
    payload = result.model_dump()
    payload["request"][exclusion] = ["q"]
    with pytest.raises(ValidationError):
        PracticeSnapshot.model_validate(payload)


def test_failed_parse_cannot_contribute_success():
    manifest = EvaluationManifest.model_validate({"samples": [{
        "id": "s", "kind": "synthetic", "expected_questions": [{"id": "q"}],
        "expected_associations": [{"id": "a", "question_id": "q"}]}]})
    try:
        predictions = PredictionSet.model_validate({"runs": [{
            "sample_id": "s", "parse_status": "invalid_json", "items": [{
                "question_id": "q", "association_id": "a", "adjudicated_complete": True}]}]})
    except ValidationError:
        return  # Rejecting contradictory failure payloads is also safe.
    result = evaluate(manifest, predictions)["synthetic"]
    assert result["question_coverage"]["numerator"] == 0
    assert result["correct_association_count"] == 0


def test_multiple_subquestion_associations_are_not_duplicate_questions():
    manifest = EvaluationManifest.model_validate({"samples": [{
        "id": "s", "kind": "synthetic", "expected_questions": [{"id": "q"}],
        "expected_associations": [
            {"id": "a1", "question_id": "q", "subquestion_id": "s1"},
            {"id": "a2", "question_id": "q", "subquestion_id": "s2"}]}]})
    predictions = PredictionSet.model_validate({"runs": [{
        "sample_id": "s", "parse_status": "parsed", "items": [
            {"question_id": "q", "subquestion_id": "s1", "association_id": "a1"},
            {"question_id": "q", "subquestion_id": "s2", "association_id": "a2"}]}]})
    result = evaluate(manifest, predictions)["synthetic"]
    assert result["duplicate_questions"] == []
    assert result["association_recall"]["rate"] == 1


def test_real_pandoc_never_silently_discards_second_formula(tmp_path):
    pypandoc = pytest.importorskip("pypandoc")
    from app.exporting import ExportError, _pandoc_formula
    try:
        node = _pandoc_formula("x$$\n\n$$y", pypandoc.get_pandoc_path(), tmp_path, {})
    except ExportError:
        return  # Explicit failure preserves the caller's knowledge of missing content.
    assert "y" in "".join(node.itertext())


def test_export_revalidates_mutated_nested_snapshot_before_converters(tmp_path):
    from app.exporting import export_snapshot
    result = build_practice([question("q", ["a"])], curriculum(), progress(), request(("a", 1)))
    result.items.append(result.items[0].model_copy(update={"order": 2}, deep=True))
    with pytest.raises(ValidationError):
        export_snapshot(result, tmp_path / "out", asset_root=tmp_path,
                        curriculum=curriculum(), current_progress=progress(),
                        pandoc_executable="/missing/pandoc", soffice_executable="/missing/soffice")
    assert not (tmp_path / "out").exists()


def test_export_rejects_withdrawn_progress_before_converters(tmp_path):
    from app.exporting import ExportError, export_snapshot
    result = build_practice([question("q", ["a"])], curriculum(), progress(), request(("a", 1)))
    with pytest.raises(ExportError, match="progress"):
        export_snapshot(result, tmp_path / "out", asset_root=tmp_path,
                        curriculum=curriculum(), current_progress=progress(["b"]),
                        pandoc_executable="/missing/pandoc", soffice_executable="/missing/soffice")
    assert not (tmp_path / "out").exists()


def test_fingerprint_ignores_subquestion_identity_and_answer_format():
    from app.ai import DeepSeekAdapter, DeepSeekConfig

    async def run():
        calls = 0

        def handler(_request):
            nonlocal calls
            calls += 1
            payload = {
                "stem": [{"kind": "text", "text": "共同题干"}],
                "answer": [], "point_ids": ["a"], "difficulty_score": 30,
                "bbox": [0, 0, 1, 1],
                "subquestions": [{"id": f"source-specific-{calls}",
                                  "stem": [{"kind": "text", "text": "求x"}],
                                  "answer": [{"kind": "text", "text": "1" if calls == 1 else "1.0"}]}],
            }
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = DeepSeekAdapter(DeepSeekConfig(api_key="synthetic", model="synthetic-model"),
                                     client=client)
            results = []
            for serial in (1, 2):
                results.append(await adapter.extract(
                    prompt="synthetic input", allowed_point_ids={"a"}, question_id=f"q{serial}",
                    canonical_id=f"c{serial}", curriculum_version="synthetic-v1",
                    asset_id=f"asset-{serial}"))
        assert results[0].question.content_fingerprint == results[1].question.content_fingerprint

    asyncio.run(run())


@pytest.mark.parametrize("content,finish_reason", [
    ("   \n\t", "stop"),
    (None, "stop"),
    ("valid", "length"),
    ("valid", "aborted"),
    ("valid", "insufficient_system_resource"),
    ("valid", "content_filter"),
])
def test_deepseek_incomplete_outputs_never_return_questions(content, finish_reason):
    from app.ai import AIResponseError, DeepSeekAdapter, DeepSeekConfig

    async def run():
        payload = {"stem": [{"kind": "text", "text": "求1+1"}],
                   "answer": [{"kind": "text", "text": "2"}],
                   "point_ids": ["a"], "difficulty_score": 10, "bbox": [0, 0, 1, 1]}
        envelope = {"choices": [{"finish_reason": finish_reason,
                                 "message": {"content": json.dumps(payload)
                                             if content == "valid" else content}}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=envelope))) as client:
            adapter = DeepSeekAdapter(DeepSeekConfig(api_key="synthetic-secret"), client=client)
            with pytest.raises(AIResponseError):
                await adapter.extract(prompt="synthetic", allowed_point_ids={"a"},
                                      question_id="q", canonical_id="c", curriculum_version="v",
                                      asset_id="asset")
    asyncio.run(run())


def test_deepseek_redirect_never_forwards_credentials_even_with_redirecting_client():
    from app.ai import AIPermanentError, DeepSeekAdapter, DeepSeekConfig

    async def run():
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(302, headers={"location": "https://other.invalid/steal"},
                                  text="synthetic-secret provider content")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     follow_redirects=True) as client:
            adapter = DeepSeekAdapter(DeepSeekConfig(api_key="synthetic-secret"), client=client)
            with pytest.raises(AIPermanentError) as error:
                await adapter.extract(prompt="synthetic", allowed_point_ids={"a"},
                                      question_id="q", canonical_id="c", curriculum_version="v",
                                      asset_id="asset")
        assert len(seen) == 1
        assert str(seen[0].url) == "https://api.deepseek.com/chat/completions"
        request_body = json.loads(seen[0].content)
        assert request_body["model"] == "deepseek-flash"
        assert request_body["thinking"] == {"type": "disabled"}
        assert request_body["response_format"] == {"type": "json_object"}
        assert "enable_thinking" not in request_body
        assert "synthetic-secret" not in str(error.value)
    asyncio.run(run())


def test_codex_timeout_stops_spawned_child_process(tmp_path, monkeypatch):
    from app import codex_cli
    from app.ai import AIResponseError
    from app.codex_cli import CodexCLIAdapter, CodexCLIConfig

    marker = tmp_path / "child-continued-after-timeout"
    started = tmp_path / "fake-cli-started"
    child_ready = tmp_path / "fake-cli-child-ready"
    begin_extraction = tmp_path / "begin-fake-extraction"
    child_code = (
        "import time\nfrom pathlib import Path\n"
        f"Path({str(child_ready)!r}).write_text('ready')\n"
        f"while not Path({str(begin_extraction)!r}).exists():\n    time.sleep(0.01)\n"
        "time.sleep(1.5)\n"
        f"Path({str(marker)!r}).write_text('continued')\n"
    )
    fake_cli = tmp_path / "fake-codex"
    fake_cli.write_text(
        f"#!{sys.executable}\n"
        "import subprocess,sys,time,json\nfrom pathlib import Path\n"
        # Deliberately exceed the extraction timeout to exercise cold startup.
        "time.sleep(1.0)\n"
        f"Path({str(started)!r}).write_text(json.dumps(sys.argv))\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o700)

    class StartupSynchronizedPopen(codex_cli.subprocess.Popen):
        """Separate a bounded fixture startup wait from the real timeout check."""

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                deadline = time.monotonic() + 15
                while not child_ready.exists():
                    if self.poll() is not None or time.monotonic() >= deadline:
                        CodexCLIAdapter._kill_process_tree(self)
                        pytest.fail("fake CLI child did not complete its bounded startup handshake")
                    time.sleep(0.01)
                begin_extraction.write_text("begin")
            return super().communicate(input=input, timeout=timeout)

    monkeypatch.setattr(codex_cli.subprocess, "Popen", StartupSynchronizedPopen)
    adapter = CodexCLIAdapter(CodexCLIConfig(executable=str(fake_cli), timeout_seconds=0.8))
    with pytest.raises(AIResponseError, match="timed out"):
        asyncio.run(adapter.extract(prompt="synthetic", allowed_point_ids={"a"},
                                    question_id="q", canonical_id="c", curriculum_version="v",
                                    asset_id="asset"))
    assert started.exists(), "fake CLI must reach the child spawn path"
    assert child_ready.exists(), "child must be running before the extraction timeout starts"
    argv = json.loads(started.read_text())
    assert "--ignore-user-config" in argv
    assert "--ephemeral" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    time.sleep(1.6)
    assert not marker.exists(), "CLI child survived adapter timeout"
