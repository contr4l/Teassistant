"""Run an independent durable database worker, without requiring a browser session."""

import argparse
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.web_jobs import run_once  # noqa: E402
from app.web_settings import WebSettings  # noqa: E402
from app.web_store import Store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = WebSettings.from_env()
    store = Store(settings.database_url)
    store.init()
    identity = "worker-" + uuid.uuid4().hex
    while True:
        worked = run_once(store, settings, identity)
        if args.once:
            break
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
