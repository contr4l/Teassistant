"""Verify a two-platform Linux release index before assigning deployment tags."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

INDEX_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}
IMAGE_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}
MAX_BYTES = 1024 * 1024


class ManifestError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate JSON key")
        result[key] = value
    return result


def validate_manifest(document):
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 2 or document.get("mediaType") not in INDEX_TYPES):
        raise ManifestError("expected a Docker or OCI schema-2 image index")
    manifests = document.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 2:
        raise ManifestError("release requires exactly two native image descriptors")
    platforms = {}
    digests = set()
    for descriptor in manifests:
        if not isinstance(descriptor, dict) or descriptor.get("mediaType") not in IMAGE_TYPES:
            raise ManifestError("release descriptor must reference a native image manifest")
        digest = descriptor.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ManifestError("release descriptor digest is invalid")
        if type(descriptor.get("size")) is not int or descriptor["size"] <= 0:
            raise ManifestError("release descriptor size must be a positive integer")
        platform = descriptor.get("platform")
        if not isinstance(platform, dict) or platform.get("os") != "linux":
            raise ManifestError("release platform must be Linux")
        arch = platform.get("architecture")
        if arch not in {"amd64", "arm64"} or arch in platforms:
            raise ManifestError("release requires amd64 and arm64 exactly once")
        variants = {None, "", "v8"} if arch == "arm64" else {None, ""}
        if platform.get("variant") not in variants:
            raise ManifestError("release uses an unsupported CPU variant")
        if digest in digests:
            raise ManifestError("native platform images must have distinct digests")
        platforms[arch] = digest
        digests.add(digest)
    if set(platforms) != {"amd64", "arm64"}:
        raise ManifestError("release does not include both required platforms")
    return {"platforms": ["linux/amd64", "linux/arm64"], "digests": platforms}


def verify_file(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ManifestError("image index exceeds the validation size limit")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ManifestError("image index is not valid UTF-8 JSON") from None
    return validate_manifest(document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_file(args.input)
    except (OSError, ManifestError, TypeError):
        print("Image manifest verification failed; require native linux/amd64 and linux/arm64 images.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
