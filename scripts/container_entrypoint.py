"""Validate safe production settings, then replace PID 1 with the requested process."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.web_settings import WebSettings  # noqa: E402


def validate_runtime_config() -> None:
    """Check production routing without reading or displaying credentials."""
    if not os.environ.get("TEASSISTANT_DATABASE_URL", "").strip():
        raise ValueError("explicit database configuration required")
    settings = WebSettings.from_env()
    if settings.model_provider != "deepseek" or settings.allow_codex:
        raise ValueError("unsafe model routing")
    if not settings.database_url or not settings.database_url.strip():
        raise ValueError("database configuration missing")
    database = make_url(settings.database_url)
    if database.get_backend_name() not in {"postgresql", "sqlite"}:
        raise ValueError("database backend unsupported")
    if database.get_backend_name() == "postgresql" and not database.host:
        raise ValueError("database host missing")
    if not settings.frontend_dist.is_dir() or not settings.curriculum_path.is_file():
        raise ValueError("runtime files missing")
    data_parent = settings.data_dir
    while not data_parent.exists() and data_parent != data_parent.parent:
        data_parent = data_parent.parent
    if not data_parent.is_dir() or not os.access(data_parent, os.W_OK):
        raise ValueError("private storage path unavailable")
    if (settings.bank_dir is None) != (settings.semantic_dir is None):
        raise ValueError("source bank configuration incomplete")
    if settings.bank_dir and not settings.bank_dir.is_dir():
        raise ValueError("source bank path unavailable")
    if settings.semantic_dir and not settings.semantic_dir.is_dir():
        raise ValueError("semantic source path unavailable")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        validate_runtime_config()
    except Exception:
        print("container configuration invalid", file=sys.stderr)
        return 78

    if args == ["--check-config"]:
        print("container configuration valid")
        return 0
    if not args or args[0] == "--check-config":
        print("container command required", file=sys.stderr)
        return 64

    try:
        os.execvpe(args[0], args, os.environ.copy())
    except OSError:
        print("container command could not be started", file=sys.stderr)
        return 126
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
