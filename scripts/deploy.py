"""Configure and operate the single-server Compose deployment without exposing secrets."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.deploy.example"


class DeploymentError(ValueError):
    pass


def initialize(env_file: Path) -> None:
    """Create once with owner-only permissions; never overwrite existing configuration."""
    env_file = env_file.absolute()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    bank_root = env_file.parent / "runtime/bank"
    text = (
        TEMPLATE.read_text()
        .replace("TEASSISTANT_DB_PASSWORD=\n", f"TEASSISTANT_DB_PASSWORD={secrets.token_hex(32)}\n")
        .replace(
            "TEASSISTANT_HOST_BANK_ROOT=./runtime/bank", f"TEASSISTANT_HOST_BANK_ROOT='{bank_root}'"
        )
    )
    descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    bank_root.mkdir(parents=True, exist_ok=True)
    print("Created private deployment configuration; existing developer .env is untouched.")


def read_config(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        raise DeploymentError("Deployment configuration missing; run init first")
    values: dict[str, str] = {}
    for number, line in enumerate(env_file.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise DeploymentError(f"Invalid deployment configuration line {number}")
        if key in values:
            raise DeploymentError(f"Duplicate configuration key at line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise DeploymentError(f"Unclosed configuration value at line {number}")
            value = value[1:-1]
        values[key] = value
    # Compose gives exported shell variables precedence over its env file.
    values.update({key: os.environ[key] for key in values if key in os.environ})
    return values


def validate_config(values: dict[str, str]) -> list[str]:
    password = values.get("TEASSISTANT_DB_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{20,128}", password):
        raise DeploymentError("Database password must be 20-128 URL-safe characters; use init")
    try:
        port = int(values.get("TEASSISTANT_PORT", "8000"))
    except ValueError:
        raise DeploymentError("TEASSISTANT_PORT must be a port number") from None
    if not 1 <= port <= 65535:
        raise DeploymentError("TEASSISTANT_PORT must be between 1 and 65535")
    try:
        ipaddress.ip_address(values.get("TEASSISTANT_BIND", "127.0.0.1"))
    except ValueError:
        raise DeploymentError("TEASSISTANT_BIND must be an IP address") from None
    if values.get("TEASSISTANT_SECURE_COOKIES", "0") not in {"0", "1"}:
        raise DeploymentError("TEASSISTANT_SECURE_COOKIES must be 0 or 1")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", values.get("COMPOSE_PROJECT_NAME", "teassistant")):
        raise DeploymentError("Invalid Compose project name")
    bank_root = Path(values.get("TEASSISTANT_HOST_BANK_ROOT", "./runtime/bank")).expanduser()
    if not bank_root.is_absolute():
        bank_root = ROOT / bank_root
    if not bank_root.is_dir():
        raise DeploymentError("Host bank root is missing; run init or create the configured folder")
    bank = values.get("TEASSISTANT_BANK_DIR", "")
    semantic = values.get("TEASSISTANT_SEMANTIC_DIR", "")
    if bool(bank) != bool(semantic):
        raise DeploymentError(
            "Configure both source bank and semantic directory, or leave both empty"
        )
    warnings = []
    for directory, filename in ((bank, "bank.json"), (semantic, "annotations.json")):
        if not directory:
            continue
        path = PurePosixPath(directory)
        if ".." in path.parts or not path.is_relative_to("/bank"):
            raise DeploymentError("Container bank paths must be inside the read-only /bank mount")
        host_path = bank_root / str(path.relative_to("/bank"))
        required = host_path / filename
        _validate_bank_path(bank_root, required)
        if not required.is_file():
            raise DeploymentError(
                "Configured bank data is incomplete; source bank and semantic files required"
            )
    if semantic:
        relative = PurePosixPath(semantic).relative_to("/bank")
        curriculum_file = bank_root / str(relative) / "curriculum.json"
        _validate_bank_path(bank_root, curriculum_file)
        if not curriculum_file.is_file():
            raise DeploymentError("Semantic directory requires curriculum.json")
    else:
        warnings.append(
            "Bank not configured: account/student setup works; practice generation is unavailable."
        )
    if not values.get("DEEPSEEK_API_KEY"):
        warnings.append("DeepSeek key missing: login works; real exam recognition is unavailable.")
    return warnings


def _validate_bank_path(root: Path, path: Path) -> None:
    """Host absolute symlinks do not retain their meaning inside a bind mount."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise DeploymentError("Bank data must stay within its mounted root")
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DeploymentError("Bank paths must not contain symbolic links")


def compose_command(env_file: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(ROOT),
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "compose.yaml"),
        *arguments,
    ]


def check(env_file: Path) -> None:
    warnings = validate_config(read_config(env_file))
    result = subprocess.run(
        compose_command(env_file, "config", "--quiet"), capture_output=True, text=True
    )
    if result.returncode:
        raise DeploymentError(
            "Compose configuration validation failed; configuration was not printed"
        )
    print("Deployment configuration and Compose syntax passed.")
    for warning in warnings:
        print(warning)


def read_accounts_file(path: Path) -> bytes:
    """Validate the private host seed before building or changing any service."""
    # This module is standard-library-only so deployment needs no host uv env.
    sys.path.insert(0, str(ROOT / "scripts"))
    from bootstrap_accounts import AccountSeedError, parse_accounts

    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or (
                os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise DeploymentError("Accounts file must be a private regular file (chmod 600)")
            if metadata.st_size > 131072:
                raise DeploymentError("Accounts file is too large")
            raw = stream.read(131073)
        accounts = parse_accounts(raw)
        if not any(account["role"] == "admin" for account in accounts):
            raise DeploymentError("Bootstrap accounts require at least one administrator")
        return raw
    except (OSError, AccountSeedError):
        raise DeploymentError("Accounts file is missing, unsafe or invalid") from None


def bootstrap(env_file: Path, accounts_file: Path, *, build: bool) -> None:
    raw = read_accounts_file(accounts_file)
    if not env_file.exists():
        initialize(env_file)
    check(env_file)
    if build:
        subprocess.run(compose_command(env_file, "build", "api"), check=True)
    subprocess.run(
        compose_command(env_file, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180"),
        check=True,
    )
    # Never put bootstrap passwords in argv, the environment, image or printed output.
    subprocess.run(
        compose_command(
            env_file,
            "exec",
            "-T",
            "api",
            "/app/.venv/bin/python",
            "/app/scripts/bootstrap_accounts.py",
            "--stdin",
        ),
        input=raw,
        capture_output=True,
        check=True,
    )
    print("Deployment ready; requested accounts initialized, existing accounts preserved.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.deploy")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "check", "build", "pull", "status", "stop"):
        sub.add_parser(command)
    up = sub.add_parser("up")
    up.add_argument("--build", action="store_true", help="build local image before startup")
    admin = sub.add_parser("admin")
    admin.add_argument("--username", required=True)
    admin.add_argument("--display-name", default="管理员")
    setup = sub.add_parser("bootstrap", help="initialize configuration, start and seed accounts")
    setup.add_argument("--build", action="store_true")
    setup.add_argument("--accounts-file", type=Path, required=True)
    args = parser.parse_args(argv)
    env_file = args.env_file.absolute()
    try:
        if args.command == "init":
            initialize(env_file)
            return 0
        if args.command == "bootstrap":
            bootstrap(env_file, args.accounts_file, build=args.build)
            return 0
        if args.command in {"stop", "status"}:
            # Recovery commands must still work when mounted data was moved.
            read_config(env_file)
        else:
            check(env_file)
        commands = {
            "check": None,
            "build": ["build", "api"],
            "pull": ["pull", "postgres", "api"],
            "status": ["ps", "--all"],
            "stop": ["stop"],
            "up": ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180"],
        }
        if args.command == "admin":
            command = [
                "exec",
                "api",
                "/app/.venv/bin/python",
                "/app/scripts/manage_web.py",
                "create-admin",
                "--username",
                args.username,
                "--display-name",
                args.display_name,
            ]
        else:
            command = commands[args.command]
        if args.command == "up" and args.build:
            subprocess.run(compose_command(env_file, "build", "api"), check=True)
        if command:
            subprocess.run(compose_command(env_file, *command), check=True)
        return 0
    except DeploymentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileExistsError:
        print("Deployment configuration already exists; nothing was overwritten.", file=sys.stderr)
        return 2
    except (OSError, subprocess.CalledProcessError):
        # Never include subprocess output, DSN, API key or the configuration value.
        print(
            "Deployment command failed; check configuration, Docker availability and local service logs.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
