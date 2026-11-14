"""Report local runtime image size, without reading configuration or running the image."""

from __future__ import annotations

import argparse
import json
import subprocess


def image_size_report(image: str, *, max_bytes: int | None = None) -> str:
    if not image or image.startswith("-") or any(char.isspace() for char in image):
        raise ValueError("Expected one Docker image reference")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise ValueError("Image size limit must be a positive integer")
    result = subprocess.run(
        [
            "docker", "image", "inspect", "--format",
            '{"platform":"{{.Os}}/{{.Architecture}}","size_bytes":{{.Size}}}',
            image,
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    metadata = json.loads(result.stdout)
    size = metadata["size_bytes"]
    if type(size) is not int or size < 0:
        raise ValueError("Invalid Docker image size")
    if max_bytes is not None:
        if size > max_bytes:
            raise ValueError(f"Image size {size} bytes exceeds limit {max_bytes} bytes")
    platform = metadata["platform"]
    if platform not in {"linux/amd64", "linux/arm64"}:
        raise ValueError("Expected a supported Linux image architecture")
    return (
        f"### Runtime image size ({platform})\n\n"
        f"{size:,} bytes / {size / 1024**2:.1f} MiB / {size / 1024**3:.2f} GiB.\n\n"
        "Docker reports the cumulative uncompressed image layer size. "
        "This excludes build cache and data volumes; it is not registry download size, "
        "RAM usage, or additional disk usage when layers are shared.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--max-bytes", type=int, help="Fail if uncompressed layers exceed this limit")
    args = parser.parse_args()
    try:
        print(image_size_report(args.image, max_bytes=args.max_bytes))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
