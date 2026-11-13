"""Synthetic manifest counterexamples; no registry or Docker engine is contacted."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "image_manifest_validator", Path(__file__).resolve().parents[2] / "scripts/verify_image_manifest.py"
)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


def _index():
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
        "manifests": [{
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "size": 1000, "digest": "sha256:" + digit * 64,
            "platform": {"os": "linux", "architecture": arch},
        } for arch, digit in (("amd64", "a"), ("arm64", "b"))],
    }


@pytest.mark.parametrize("oci", [False, True])
def test_native_release_accepts_only_the_two_required_architectures(oci):
    index = _index()
    if oci:
        index["mediaType"] = "application/vnd.oci.image.index.v1+json"
        for descriptor in index["manifests"]:
            descriptor["mediaType"] = "application/vnd.oci.image.manifest.v1+json"
    index["manifests"][1]["platform"]["variant"] = "v8"
    result = manifest.validate_manifest(index)
    assert result["platforms"] == ["linux/amd64", "linux/arm64"]
    assert result["digests"]["arm64"] != result["digests"]["amd64"]


@pytest.mark.parametrize("damage", [
    "missing_arm", "duplicate_amd64", "extra_attestation", "wrong_os", "invalid_digest",
    "same_digest", "wrong_schema", "nested_index", "boolean_size", "empty_size", "arm_v9",
])
def test_bad_release_indexes_never_pass_the_publication_gate(damage):
    index = copy.deepcopy(_index())
    descriptors = index["manifests"]
    if damage == "missing_arm":
        descriptors.pop()
    elif damage == "duplicate_amd64":
        descriptors[1]["platform"]["architecture"] = "amd64"
    elif damage == "extra_attestation":
        descriptors.append({"platform": {"os": "unknown", "architecture": "unknown"}})
    elif damage == "wrong_os":
        descriptors[1]["platform"]["os"] = "windows"
    elif damage == "invalid_digest":
        descriptors[1]["digest"] = "sha256:short"
    elif damage == "same_digest":
        descriptors[1]["digest"] = descriptors[0]["digest"]
    elif damage == "wrong_schema":
        index["schemaVersion"] = True
    elif damage == "nested_index":
        descriptors[1]["mediaType"] = index["mediaType"]
    elif damage == "boolean_size":
        descriptors[1]["size"] = True
    elif damage == "empty_size":
        descriptors[1]["size"] = 0
    elif damage == "arm_v9":
        descriptors[1]["platform"]["variant"] = "v9"
    with pytest.raises(manifest.ManifestError):
        manifest.validate_manifest(index)


def test_cli_never_echoes_invalid_manifest_or_private_path(tmp_path, capsys):
    path = tmp_path / "SYNTHETIC_PRIVATE_PATH"
    path.write_text('{"schemaVersion": "SYNTHETIC_SECRET"}')
    assert manifest.main(["--input", str(path)]) == 1
    output = capsys.readouterr()
    assert "SYNTHETIC" not in output.out + output.err


def test_file_reader_rejects_duplicate_json_keys_and_oversized_inputs(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"schemaVersion": 2,"schemaVersion": 2}')
    with pytest.raises(manifest.ManifestError, match="duplicate"):
        manifest.verify_file(path)
    path.write_bytes(b" " * (manifest.MAX_BYTES + 1))
    with pytest.raises(manifest.ManifestError, match="size"):
        manifest.verify_file(path)


def test_cli_outputs_validated_platforms_for_a_real_file(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_index()))
    assert manifest.main(["--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["platforms"] == ["linux/amd64", "linux/arm64"]
