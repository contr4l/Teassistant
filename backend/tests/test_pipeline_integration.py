"""Synthetic HTTP response → contracts → allocator → real document converters.

The real converter test is opt-in through TEASSISTANT_SOFFICE; there is no live AI call.
"""

import asyncio
import json
import os
import zipfile
from pathlib import Path

import httpx
import pytest
from app.ai import DeepSeekAdapter, DeepSeekConfig
from app.contracts import Curriculum, KnowledgePoint, PracticeRequest, Quota, StudentProgress
from app.exporting import export_snapshot
from app.practice import build_practice


def test_synthetic_pipeline_with_real_converters(tmp_path):
    soffice = os.getenv("TEASSISTANT_SOFFICE")
    if not soffice:
        pytest.skip("set TEASSISTANT_SOFFICE for real converter integration; no live AI")
    pypandoc = pytest.importorskip("pypandoc")
    payload = {
        "id": "synthetic-1",
        "canonical_id": "synthetic-1",
        "content_fingerprint": "synthetic-fp",
        "curriculum_version": "synthetic-v1",
        "stem": [
            {"kind": "text", "text": "计算："},
            {"kind": "formula", "latex": r"\frac{1}{2}+\frac{1}{2}"},
        ],
        "answer": [
            {"kind": "text", "text": "ANSWER_ONLY_MARKER"},
            {"kind": "formula", "latex": "1"},
        ],
        "point_ids": ["fraction"],
        "difficulty_score": 10,
        "sources": [{"asset_id": "synthetic", "page": 1, "bbox": [0, 0, 1, 1]}],
    }
    # Recognition fields only; provenance IDs are supplied by our application, never the model.
    payload = {
        key: value
        for key, value in payload.items()
        if key not in {"id", "canonical_id", "content_fingerprint", "curriculum_version", "sources"}
    }
    payload["bbox"] = [0, 0, 1, 1]

    async def extract():
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "model": "synthetic-mock",
                    "choices": [
                        {"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = DeepSeekAdapter(
                DeepSeekConfig(api_key="synthetic-test-key", model="synthetic-mock"), client=client
            )
            return await adapter.extract(
                prompt="synthetic test data",
                allowed_point_ids={"fraction"},
                question_id="synthetic-1",
                canonical_id="synthetic-1",
                curriculum_version="synthetic-v1",
                asset_id="synthetic",
                page=1,
            )

    result = asyncio.run(extract())
    c = Curriculum(version="synthetic-v1", points=[KnowledgePoint(id="fraction", name="分数")])
    p = StudentProgress(
        student_id="demo",
        revision="1",
        curriculum_version=c.version,
        learned_point_ids=["fraction"],
    )
    snapshot = build_practice(
        [result.question], c, p, PracticeRequest(quotas=[Quota(point_id="fraction", count=1)])
    )
    files = export_snapshot(
        snapshot,
        tmp_path / "export",
        asset_root=tmp_path,
        curriculum=c,
        current_progress=p,
        pandoc_executable=pypandoc.get_pandoc_path(),
        soffice_executable=soffice,
    )
    for path in [files.student_pdf, files.teacher_pdf]:
        assert Path(path).read_bytes().startswith(b"%PDF-")
    with zipfile.ZipFile(files.student_docx) as doc:
        student_xml = doc.read("word/document.xml").decode()
    with zipfile.ZipFile(files.teacher_docx) as doc:
        teacher_xml = doc.read("word/document.xml").decode()
    assert "ANSWER_ONLY_MARKER" not in student_xml
    assert "ANSWER_ONLY_MARKER" in teacher_xml
    assert "oMath" in student_xml and "oMath" in teacher_xml
