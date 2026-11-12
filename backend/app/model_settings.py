"""Private, write-only administrator credentials and versioned local price settings."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path

from app.ai import DeepSeekConfig

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
DEFAULT_PRICES = {
    "input_cache_hit": "0.040000",
    "input_cache_miss": "2.000000",
    "output": "8.000000",
}
ESTIMATION_NOTE = "本平台按请求返回的 token 用量与配置固定单价估算，非官方扣费账单；未自动区分峰谷时段，缺缓存细节按未命中估算。"
INPUT_UPPER_BOUND = 1_048_576


class ModelSettingsError(ValueError):
    pass


class ModelSettingsConflict(ModelSettingsError):
    pass


def validate_prices(prices: dict) -> dict[str, str]:
    if not isinstance(prices, dict) or set(prices) != set(DEFAULT_PRICES):
        raise ModelSettingsError("请输入完整的三项模型单价")
    result = {}
    for key in DEFAULT_PRICES:
        raw = prices[key]
        if not isinstance(raw, str) or not re.fullmatch(r"\d{1,7}(?:\.\d{1,6})?", raw):
            raise ModelSettingsError("单价须为最多六位小数的正金额")
        try:
            amount = Decimal(raw)
            if not amount.is_finite() or not 0 < amount <= 1_000_000:
                raise InvalidOperation
        except InvalidOperation:
            raise ModelSettingsError("单价超出允许范围") from None
        result[key] = format(amount, ".6f")
    return result


def reservation_credit(prices: dict, max_tokens: int = 8192) -> str:
    prices = validate_prices(prices)
    if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
        raise ModelSettingsError("模型输出上限无效")
    amount = (
        INPUT_UPPER_BOUND
        * max(Decimal(prices["input_cache_hit"]), Decimal(prices["input_cache_miss"]))
        + max_tokens * Decimal(prices["output"])
    ) / 1_000_000
    return format(amount.quantize(Decimal("0.000001"), rounding=ROUND_CEILING), ".6f")


class ModelSettingsStore:
    def __init__(self, data_dir: Path):
        self.directory = Path(data_dir) / "admin-model"
        self.path = self.directory / "deepseek.json"

    def _read(self) -> dict:
        if self.path.is_symlink() or self.directory.is_symlink():
            raise ModelSettingsError("模型配置存储路径无效")
        if not self.path.exists():
            return {
                "revision": 0,
                "api_key": None,
                "model": os.getenv("DEEPSEEK_MODEL") or MODEL,
                "prices": dict(DEFAULT_PRICES),
            }
        try:
            value = json.loads(self.path.read_bytes())
            if not isinstance(value, dict) or type(value.get("revision")) is not int:
                raise ValueError
            validate_prices(value["prices"])
            if value.get("model") != MODEL:
                raise ValueError
            if value.get("api_key") is not None and not isinstance(value["api_key"], str):
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise ModelSettingsError("模型配置无法读取，请管理员检查私有配置文件") from None

    def public(self) -> dict:
        value = self._read()
        admin_key = value.get("api_key")
        environment_key = os.getenv("DEEPSEEK_API_KEY")
        return {
            "revision": value["revision"],
            "key_configured": bool(admin_key or environment_key),
            "key_source": "admin" if admin_key else "environment" if environment_key else "none",
            "model": value["model"],
            "base_url": BASE_URL,
            "prices": value["prices"],
            "currency": "CNY",
            "reservation_credit": reservation_credit(value["prices"]),
            "estimation_note": ESTIMATION_NOTE,
        }

    def save(
        self,
        *,
        expected_revision: int,
        api_key: str | None,
        model: str,
        prices: dict,
        actor_id: str,
    ) -> dict:
        if model != MODEL:
            raise ModelSettingsError("本批仅支持已核准的 DeepSeek Flash 视觉模型")
        prices = validate_prices(prices)
        if api_key is not None and not isinstance(api_key, str):
            raise ModelSettingsError("API 密钥格式无效")
        key = api_key.strip() if api_key is not None else None
        if key and (not 8 <= len(key) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise ModelSettingsError("API 密钥格式无效")
        if self.directory.is_symlink():
            raise ModelSettingsError("模型配置存储路径无效")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        lock_path = self.directory / "settings.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(lock_path, flags, 0o600), "r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self._read()
            if type(expected_revision) is not int or expected_revision != current["revision"]:
                raise ModelSettingsConflict("模型配置已更新，请刷新后再保存")
            payload = {
                "revision": current["revision"] + 1,
                "api_key": key or current.get("api_key"),
                "model": model,
                "prices": prices,
                "updated_by": actor_id,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            descriptor, name = tempfile.mkstemp(prefix=".deepseek-", dir=self.directory)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(payload, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return self.public()

    def runtime(self, *, timeout: float = 360) -> tuple[DeepSeekConfig, dict]:
        value = self._read()
        key = value.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise ModelSettingsError("尚未配置 DeepSeek API 密钥，请联系管理员")
        if (
            not isinstance(key, str)
            or not 8 <= len(key) <= 512
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
        ):
            raise ModelSettingsError("DeepSeek API 密钥格式无效，请联系管理员")
        if value["model"] != MODEL:
            raise ModelSettingsError("模型未核准，管理员需选择 DeepSeek Flash")
        config = DeepSeekConfig(
            api_key=key, model=MODEL, base_url=BASE_URL, timeout_seconds=timeout
        )
        return config, {
            "revision": value["revision"],
            "prices": value["prices"],
            "model": MODEL,
            "key_source": "admin" if value.get("api_key") else "environment",
        }
