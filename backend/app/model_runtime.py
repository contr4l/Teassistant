"""Owner-bound DeepSeek HTTP metering shared by workers and their child processes."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.exam_models import DeepSeekExamAdapter
from app.model_settings import BASE_URL, MODEL, ModelSettingsError, ModelSettingsStore

CONTEXT_ENV = "TEASSISTANT_BILLING_CONTEXT"
REQUIRED_ENV = "TEASSISTANT_REQUIRE_BILLING"


class MeteringError(ValueError):
    pass


@dataclass(frozen=True)
class BillingContext:
    user_id: str
    job_id: str
    lease_token: str

    @classmethod
    def from_job(cls, job: dict):
        return cls(job["owner_id"], job["id"], job["lease_token"])

    def check_job(self, store) -> None:
        job = store.get_job(self.job_id, self.user_id)
        if (
            not job
            or job.get("owner_id") != self.user_id
            or job.get("status") != "running"
            or job.get("lease_token") != self.lease_token
        ):
            raise MeteringError("模型调用任务归属或租约无效")
        expiry = job.get("lease_expires_at")
        if not expiry or datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= datetime.now(UTC):
            raise MeteringError("模型调用任务租约已过期")

    def environment(self, settings) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("DEEPSEEK_API_KEY", None)
        # Credentials are read from the private model configuration, or explicitly
        # inherited only as an environment fallback; never put them in argv/jobs.
        if os.getenv("DEEPSEEK_API_KEY"):
            env["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
        env.update(
            {
                CONTEXT_ENV: json.dumps(self.__dict__),
                REQUIRED_ENV: "1",
                "TEASSISTANT_DATABASE_URL": settings.database_url,
                "TEASSISTANT_DATA_DIR": str(settings.data_dir),
            }
        )
        return env


class MeteredTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        store,
        context: BillingContext,
        stage: str,
        snapshot: dict,
        inner: httpx.AsyncBaseTransport | None = None,
    ):
        from app.model_billing import BillingStore

        self.store = store
        self.billing = BillingStore(store.engine)
        self.context = context
        self.stage = stage
        self.snapshot = snapshot
        self.operation_id = uuid.uuid4().hex
        self.attempt_no = 0
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or str(request.url) != BASE_URL + "/chat/completions":
            raise MeteringError("计费通路仅允许官方 DeepSeek 模型端点")
        try:
            body = json.loads(request.content)
            maximum = body["max_tokens"]
            if body["model"] != MODEL or type(maximum) is not int or not 1 <= maximum <= 8192:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise MeteringError("模型请求上界未核准") from None
        self.context.check_job(self.store)
        self.attempt_no += 1
        reservation = await asyncio.to_thread(
            self.billing.reserve_attempt,
            user_id=self.context.user_id,
            job_id=self.context.job_id,
            stage=self.stage,
            operation_id=self.operation_id,
            attempt_no=self.attempt_no,
            model=MODEL,
            config_revision=self.snapshot["revision"],
            prices=self.snapshot["prices"],
            max_output_tokens=maximum,
        )
        if reservation.get("created") is not True:
            raise MeteringError("该计费尝试已处理，不重复发送请求")
        attempt_id = reservation["id"]
        try:
            response = await self.inner.handle_async_request(request)
            content = await response.aread()
        except BaseException as exc:
            # A cancelled process may leave an active reservation; it remains
            # held even if this write itself cannot finish. Never refund blindly.
            self.billing.settle_attempt(
                attempt_id,
                usage=None,
                http_status=None,
                error_code="timeout"
                if isinstance(exc, httpx.TimeoutException)
                else "network_error",
            )
            raise
        usage = None
        try:
            decoded = json.loads(content)
            if isinstance(decoded, dict):
                usage = decoded.get("usage")
        except (ValueError, TypeError):
            pass
        await asyncio.to_thread(
            self.billing.settle_attempt,
            attempt_id,
            usage=usage,
            http_status=response.status_code,
            error_code=None if isinstance(usage, dict) else "unknown_usage",
        )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class MeteredExamAdapter:
    def __init__(
        self,
        *,
        store,
        settings,
        context: BillingContext,
        network_factory=None,
        timeout: float | None = None,
    ):
        self.store = store
        self.settings = settings
        self.context = context
        self.context.check_job(store)
        self.config, self.snapshot = ModelSettingsStore(settings.data_dir).runtime(
            timeout=timeout or settings.model_timeout_seconds
        )
        self.network_factory = network_factory

    async def assess(self, *, task: str, prompt: str, image_paths: list):
        transport = MeteredTransport(
            store=self.store,
            context=self.context,
            stage=task,
            snapshot=self.snapshot,
            inner=self.network_factory() if self.network_factory else None,
        )
        async with httpx.AsyncClient(
            transport=transport, timeout=self.config.timeout_seconds, follow_redirects=False
        ) as client:
            return await DeepSeekExamAdapter(self.config, client=client).assess(
                task=task, prompt=prompt, image_paths=image_paths
            )


def adapter_from_environment(*, timeout: float = 360):
    """Internal CLI bridge. A web child may never silently use an unmetered key."""
    from app.web_settings import WebSettings
    from app.web_store import Store

    try:
        raw = json.loads(os.environ[CONTEXT_ENV])
        if set(raw) != {"user_id", "job_id", "lease_token"}:
            raise ValueError
        if not all(isinstance(v, str) and v for v in raw.values()):
            raise ValueError
        context = BillingContext(**raw)
    except (KeyError, ValueError, TypeError):
        raise MeteringError("Web 模型调用缺少有效计费上下文") from None
    settings = WebSettings.from_env()
    if settings.database_url in {"sqlite://", "sqlite:///:memory:"}:
        raise ModelSettingsError("子进程计费需要共享持久数据库")
    store = Store(settings.database_url)
    return MeteredExamAdapter(store=store, settings=settings, context=context, timeout=timeout)
