#!/usr/bin/env python3
"""Compute the immutable tools image reference from its complete build input."""

from __future__ import annotations

import argparse
import hashlib
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/contr4l/teassistant-tools"


def content_hash(root: Path = ROOT) -> str:
    """Hash Dockerfile.tools and its explicit application inputs."""
    dockerfile = root / "Dockerfile.tools"
    tools_dir = root / "tools"
    if not dockerfile.is_file() or not tools_dir.is_dir():
        raise FileNotFoundError("Dockerfile.tools and tools/ are required")
    # Dockerfile.tools copies these two files explicitly. Keep the reference tied
    # to exactly those inputs so imports/tests cannot pollute it with pyc caches.
    tool_inputs = (tools_dir / "worker.py", tools_dir / "requirements.txt")
    if any(not path.is_file() for path in tool_inputs):
        raise FileNotFoundError("tools/worker.py and tools/requirements.txt are required")
    docker_text = dockerfile.read_text(encoding="utf-8")
    copy_sources: set[str] = set()
    for line in docker_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ADD "):
            raise ValueError("Dockerfile.tools must use explicit COPY inputs, not ADD")
        if not stripped.startswith("COPY "):
            continue
        words = shlex.split(stripped)
        sources = [word for word in words[1:-1] if not word.startswith("--")]
        copy_sources.update(sources)
    expected_sources = {path.relative_to(root).as_posix() for path in tool_inputs}
    if copy_sources != expected_sources:
        raise ValueError("Dockerfile.tools COPY sources must match the hashed tools allowlist")
    files = [dockerfile, *tool_inputs]
    digest = hashlib.sha256(b"teassistant-tools-image-v1\0")
    for path in files:
        if path.is_symlink():
            raise ValueError("tools image inputs may not contain symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--hash-only", action="store_true", help="print the content SHA256")
    mode.add_argument("--tag-only", action="store_true", help="print only the image tag")
    args = parser.parse_args()
    value = content_hash()
    tag = f"sha256-{value}"
    print(value if args.hash_only else tag if args.tag_only else f"{IMAGE}:{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
