"""Container health probes that reveal no DSN or private application data."""

from __future__ import annotations

import argparse
import http.client
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.web_settings import WebSettings  # noqa: E402

WORKER_COMMAND = ("/app/.venv/bin/python", "/app/scripts/run_web_worker.py")


def api_http_ready() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=2)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        return response.status == 200
    except Exception:
        return False
    finally:
        connection.close()


def database_ready(database_url: str | None = None) -> bool:
    engine = None
    try:
        settings = WebSettings.from_env()
        url = make_url(settings.database_url if database_url is None else database_url)
        if url.get_backend_name() == "postgresql":
            engine = create_engine(
                url,
                connect_args={
                    "connect_timeout": 2,
                    "options": "-c statement_timeout=2000",
                },
                pool_pre_ping=True,
            )
        elif url.get_backend_name() == "sqlite":
            engine = create_engine(url, connect_args={"timeout": 2})
        else:
            return False
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


def worker_pid1_ready(cmdline: bytes | None = None) -> bool:
    try:
        raw = Path("/proc/1/cmdline").read_bytes() if cmdline is None else cmdline
    except OSError:
        return False
    args = raw.split(b"\0")
    if args and args[-1] == b"":
        args.pop()
    expected = [part.encode() for part in WORKER_COMMAND]
    return args == expected


def check_role(role: str) -> bool:
    if role == "api":
        return api_http_ready() and database_ready()
    if role == "worker":
        return worker_pid1_ready() and database_ready()
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("api", "worker"), required=True)
    args = parser.parse_args(argv)
    if not check_role(args.role):
        print("container health check failed", file=sys.stderr)
        return 1
    print("container health check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
