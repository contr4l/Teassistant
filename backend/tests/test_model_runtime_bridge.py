"""A real child process must debit the same wallet; its provider is a synthetic transport."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from app.model_billing import BillingStore
from app.model_runtime import BillingContext
from app.model_settings import DEFAULT_PRICES, ModelSettingsStore
from app.web_settings import WebSettings
from app.web_store import Store


def test_child_process_uses_shared_wallet_before_schema_failure(tmp_path):
    settings = replace(
        WebSettings(),
        database_url="sqlite:///" + str(tmp_path / "shared.sqlite"),
        data_dir=tmp_path / "private",
    )
    store = Store(settings.database_url)
    store.init()
    BillingStore(store.engine).save_defaults("0", 1, "synthetic-fixture")
    teacher = store.create_user("synthetic-bridge", "synthetic-password", "合成桥接老师")
    exam = store.create_record("exam", teacher["id"], {"title": "合成考试"})
    queued = store.enqueue_job(teacher["id"], "analysis", exam["id"], {})
    job = store.claim_job("synthetic-parent-worker")
    assert job["id"] == queued["id"]
    secret = "synthetic-key-no-real-credentials"
    ModelSettingsStore(settings.data_dir).save(
        expected_revision=0,
        api_key=secret,
        model="deepseek-flash",
        prices=DEFAULT_PRICES,
        actor_id=teacher["id"],
    )
    billing = BillingStore(store.engine)
    billing.top_up(teacher["id"], "20", "synthetic-initial-credit", teacher["id"])
    environment = BillingContext.from_job(job).environment(settings)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    program = """
import asyncio
import httpx
from app.model_runtime import adapter_from_environment
from app.exam_models import ExamModelResponseError

def respond(request):
    return httpx.Response(200, json={
        "choices": [{"finish_reason":"stop", "message":{"content":"{}"}}],
        "usage": {"prompt_tokens":1000, "completion_tokens":50,
                  "prompt_cache_hit_tokens":100, "prompt_cache_miss_tokens":900}
    })

adapter=adapter_from_environment()
adapter.network_factory=lambda: httpx.MockTransport(respond)
try:
    asyncio.run(adapter.assess(task="paper", prompt="synthetic-only", image_paths=[]))
except ExamModelResponseError:
    print("synthetic response rejected after recording its usage")
else:
    raise AssertionError("synthetic empty business schema must fail")
"""
    child = subprocess.run(
        [sys.executable, "-c", program], env=environment, capture_output=True, text=True, timeout=20
    )
    assert child.returncode == 0, child.stderr
    assert secret not in child.stdout + child.stderr
    wallet = billing.get_wallet(teacher["id"])
    assert wallet["used_credit"] == "0.002204"
    assert wallet["reserved_credit"] == "0.000000"
    assert wallet["remaining_credit"] == "19.997796"
    assert secret not in json.dumps(wallet)
    store.close()
