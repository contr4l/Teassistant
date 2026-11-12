"""Initialize the teacher web store and explicitly manage its first users."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.prompt_templates import DEFAULT_PROMPTS  # noqa: E402
from app.web_settings import WebSettings  # noqa: E402
from app.web_store import Store, StoreConflict  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "create-admin", "list-users", "prompts"))
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--database-url")
    args = parser.parse_args()

    settings = WebSettings.from_env()
    database_url = args.database_url or settings.database_url
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(database_url, session_hours=settings.session_hours)
    try:
        store.init()
        if args.command == "init":
            store.ensure_prompts(DEFAULT_PROMPTS)
            print("web store initialized")
        elif args.command == "prompts":
            store.ensure_prompts(DEFAULT_PROMPTS)
            print(f"prompt stages available: {len(store.list_prompts())}")
        elif args.command == "list-users":
            for user in store.list_users():
                print(f"{user['role']}\t{user['username']}\t{user['display_name']}")
        else:
            username = args.username or input("Admin username: ").strip()
            display_name = args.display_name or input("Display name: ").strip()
            password = getpass.getpass("Admin password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                parser.error("passwords do not match")
            try:
                user = store.create_user(username, password, display_name, role="admin")
            except StoreConflict:
                parser.error("username already exists")
            print(f"created admin {user['username']} ({user['id']})")
    finally:
        store.close()


if __name__ == "__main__":
    main()
