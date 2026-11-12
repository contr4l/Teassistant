"""Validate private seed accounts using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 128 * 1024
_ACCOUNT_FIELDS = {"username", "password", "display_name", "role"}
_TOP_FIELDS = {"version", "users"}


class AccountSeedError(ValueError):
    """Fixed-text validation error that never includes supplied account data."""

    def __init__(self) -> None:
        super().__init__("账号配置无效")


def _object_no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AccountSeedError()
        result[key] = value
    return result


def parse_accounts(raw: bytes) -> list[dict[str, str]]:
    """Parse and normalize a bounded version-1 account seed document."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_INPUT_BYTES:
        raise AccountSeedError()
    try:
        document = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_object_no_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, AccountSeedError, RecursionError):
        raise AccountSeedError() from None
    if (
        type(document) is not dict
        or set(document) != _TOP_FIELDS
        or type(document.get("version")) is not int
        or document["version"] != 1
        or type(document.get("users")) is not list
        or not 1 <= len(document["users"]) <= 100
    ):
        raise AccountSeedError()

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for user in document["users"]:
        if type(user) is not dict or set(user) != _ACCOUNT_FIELDS:
            raise AccountSeedError()
        if any(type(user[field]) is not str for field in _ACCOUNT_FIELDS):
            raise AccountSeedError()
        try:
            username = user["username"].strip().casefold()
            display_name = user["display_name"].strip()
            username.encode("utf-8", "strict")
            display_name.encode("utf-8", "strict")
            user["password"].encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise AccountSeedError() from None
        password = user["password"]
        role = user["role"]
        if (
            not username
            or len(username) > 120
            or username in seen
            or not password
            or len(password) > 1024
            or not display_name
            or len(display_name) > 200
            or role not in {"admin", "teacher"}
        ):
            raise AccountSeedError()
        seen.add(username)
        result.append(
            {
                "username": username,
                "password": password,
                "display_name": display_name,
                "role": role,
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdin", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not args.stdin:
        parser.error("--stdin is required")
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        accounts = parse_accounts(raw)
    except AccountSeedError:
        print("账号配置无效", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    try:
        from app.web_settings import WebSettings
        from app.web_store import Store, StoreConflict
    except Exception:
        print("bootstrap failed", file=sys.stderr)
        return 4
    try:
        settings = WebSettings.from_env()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.database_url, session_hours=settings.session_hours)
        try:
            store.init()
            outcome = store.bootstrap_users(accounts)
        finally:
            store.close()
    except StoreConflict:
        print("bootstrap account conflict", file=sys.stderr)
        return 3
    except Exception:
        # Do not print a traceback or database URL; those may contain secrets.
        print("bootstrap failed", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "created": outcome["created"],
                "skipped": outcome["skipped"],
                "created_count": len(outcome["created"]),
                "skipped_count": len(outcome["skipped"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
