"""Fail-closed client for the optional standalone document tools service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TOOLS_VERSION = "tools-v1"
TOOLS_URL_ENV = "TEASSISTANT_TOOLS_URL"


class ToolClientError(RuntimeError):
    """The configured tools service could not safely complete a request."""


def tools_url() -> str | None:
    """Return the configured service base URL, or None for local development."""
    value = os.environ.get(TOOLS_URL_ENV)
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ToolClientError(f"{TOOLS_URL_ENV} is set but empty")
    return value.rstrip("/")


def tools_enabled() -> bool:
    return tools_url() is not None


def _request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = tools_url()
    if base is None:
        raise ToolClientError("remote tools are not configured")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        },
        method="GET" if data is None else "POST",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=300) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ToolClientError(f"remote tools response {path} exceeds the size limit")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolClientError(f"remote tools request {path} failed ({type(exc).__name__})") from None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolClientError(f"remote tools response {path} is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("version") != TOOLS_VERSION:
        raise ToolClientError(f"remote tools response {path} has an unsupported schema version")
    return value


def _command_result(path: str, payload: dict[str, str]) -> dict[str, Any]:
    result = _request(path, payload)
    if (
        not isinstance(result.get("returncode"), int)
        or isinstance(result.get("returncode"), bool)
        or not isinstance(result.get("stdout"), str)
        or not isinstance(result.get("stderr"), str)
    ):
        raise ToolClientError(f"remote tools response {path} does not match the command schema")
    return result


def convert_word_pdf(source: Path, output_dir: Path, profile_dir: Path) -> dict[str, Any]:
    return _command_result(
        "/v1/word-pdf",
        {
            "source": str(source.resolve()),
            "output_dir": str(output_dir.resolve()),
            "profile_dir": str(profile_dir.resolve()),
        },
    )


def convert_tex_pdf(source: Path, output_dir: Path) -> dict[str, Any]:
    return _command_result(
        "/v1/tex-pdf",
        {"source": str(source.resolve()), "output_dir": str(output_dir.resolve())},
    )


def convert_pandoc(source: Path, target: Path) -> dict[str, Any]:
    return _command_result(
        "/v1/pandoc", {"source": str(source.resolve()), "target": str(target.resolve())}
    )


def render_screenshot(html: str) -> tuple[bytes, int, int]:
    import base64
    import binascii

    result = _request("/v1/screenshot", {"html": html})
    encoded = result.get("png_base64")
    width, height = result.get("width"), result.get("height")
    if (
        not isinstance(encoded, str)
        or not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or width < 1
        or height < 1
    ):
        raise ToolClientError("remote tools screenshot response does not match the schema")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolClientError("remote tools returned invalid screenshot base64") from exc
    return png, width, height
