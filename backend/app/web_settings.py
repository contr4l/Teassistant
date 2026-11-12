"""Explicit single-server web settings; no credentials or source files in defaults."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WebSettings:
    database_url: str = "sqlite:///work/web/teassistant.db"
    data_dir: Path = ROOT / "work/web/private"
    curriculum_path: Path = ROOT / "data/curricula/junior-math-2022-draft-v2.json"
    bank_dir: Path | None = None
    semantic_dir: Path | None = None
    frontend_dist: Path = ROOT / "frontend/dist"
    allow_codex: bool = False
    model_provider: str = "deepseek"
    codex_executable: str = "codex"
    codex_model: str = ""
    secure_cookies: bool = False
    cookie_name: str = "teassistant_session"
    session_hours: int = 24
    max_upload_bytes: int = 30 * 1024 * 1024
    max_exam_files: int = 60
    model_timeout_seconds: int = 360
    soffice_executable: str | None = None
    xelatex_executable: str | None = None
    redis_url: str = "redis://localhost:6379/0"

    @classmethod
    def from_env(cls):
        def path(name, default=None):
            value = os.getenv(name)
            return Path(value).expanduser().resolve() if value else default

        return cls(
            database_url=os.getenv("TEASSISTANT_DATABASE_URL", cls.database_url),
            data_dir=path("TEASSISTANT_DATA_DIR", cls.data_dir),
            curriculum_path=path("TEASSISTANT_CURRICULUM", cls.curriculum_path),
            bank_dir=path("TEASSISTANT_BANK_DIR"),
            semantic_dir=path("TEASSISTANT_SEMANTIC_DIR"),
            frontend_dist=path("TEASSISTANT_FRONTEND_DIST", cls.frontend_dist),
            allow_codex=os.getenv("TEASSISTANT_ALLOW_CODEX") == "1",
            model_provider=os.getenv("TEASSISTANT_MODEL_PROVIDER", "deepseek"),
            codex_executable=os.getenv("CODEX_EXECUTABLE", "codex"),
            codex_model=os.getenv("CODEX_MODEL", ""),
            secure_cookies=os.getenv("TEASSISTANT_SECURE_COOKIES") == "1",
            soffice_executable=os.getenv("TEASSISTANT_SOFFICE"),
            xelatex_executable=os.getenv("TEASSISTANT_XELATEX"),
            redis_url=os.getenv("TEASSISTANT_REDIS_URL", cls.redis_url),
        )
