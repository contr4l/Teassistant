from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("tool_image_ref", ROOT / "scripts/tool_image_ref.py")
assert SPEC and SPEC.loader
tool_ref = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool_ref)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "Dockerfile.tools").write_text(
        "FROM python:3.13-slim\n"
        "COPY tools/requirements.txt ./tools/requirements.txt\n"
        "COPY tools/worker.py ./tools/worker.py\n",
        encoding="utf-8",
    )
    (tmp_path / "tools/worker.py").write_text("print('tools-v1')\n", encoding="utf-8")
    (tmp_path / "tools/requirements.txt").write_text("playwright==1.65.0\n", encoding="utf-8")
    return tmp_path


def test_tool_reference_is_stable_and_covers_only_tool_build_inputs(tmp_path):
    root = _tree(tmp_path)
    initial = tool_ref.content_hash(root)
    assert tool_ref.content_hash(root) == initial
    (root / "backend/app/business.py").parent.mkdir(parents=True)
    (root / "backend/app/business.py").write_text("business change\n", encoding="utf-8")
    assert tool_ref.content_hash(root) == initial
    cache = root / "tools/__pycache__"
    cache.mkdir()
    (cache / "worker.cpython-313.pyc").write_bytes(b"generated")
    (root / "tools/.DS_Store").write_bytes(b"generated")
    assert tool_ref.content_hash(root) == initial
    (root / "tools/worker.py").write_text("print('tools-v2')\n", encoding="utf-8")
    assert tool_ref.content_hash(root) != initial


def test_tool_reference_rejects_symlinked_build_input(tmp_path):
    root = _tree(tmp_path)
    worker = root / "tools/worker.py"
    worker.unlink()
    target = root / "outside.py"
    target.write_text("outside\n", encoding="utf-8")
    worker.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        tool_ref.content_hash(root)


def test_tool_reference_rejects_unhashed_docker_copy_source(tmp_path):
    root = _tree(tmp_path)
    (root / "Dockerfile.tools").write_text(
        (root / "Dockerfile.tools").read_text(encoding="utf-8")
        + "COPY tools/assets ./tools/assets\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="COPY sources"):
        tool_ref.content_hash(root)


def test_tools_image_contains_no_business_build_or_runtime_content():
    dockerfile = (ROOT / "Dockerfile.tools").read_text(encoding="utf-8")
    assert "COPY tools/requirements.txt" in dockerfile
    assert "COPY --chown=0:0 tools/worker.py" in dockerfile
    for forbidden in ("backend/", "scripts/", "frontend/", "pyproject.toml", "uv.lock"):
        assert forbidden not in dockerfile
    for required in ("chromium", "libreoffice-writer", "pandoc", "texlive-xetex", "fonts-noto-cjk"):
        assert required in dockerfile


def test_business_worker_target_uses_python_export_group_without_os_toolchain():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    worker = dockerfile.split("AS worker", 1)[1].split("# Legacy full runtime", 1)[0]
    assert "COPY --from=python-dependencies /app/.venv" in worker
    assert "TMPDIR=/app/work/tmp" in worker
    assert "chromium" not in worker and "libreoffice" not in worker and "texlive" not in worker
    assert "AS runtime" in dockerfile


def test_smoke_compose_wires_tools_only_when_requested(tmp_path):
    smoke_spec = importlib.util.spec_from_file_location(
        "container_smoke_for_tools", ROOT / "scripts/container_smoke.py"
    )
    assert smoke_spec and smoke_spec.loader
    smoke = importlib.util.module_from_spec(smoke_spec)
    smoke_spec.loader.exec_module(smoke)
    legacy = smoke._compose_text("worker:local", tmp_path / "share", "pw")
    assert "  tools:" not in legacy
    config = smoke._compose_text(
        "worker:local", tmp_path / "share", "pw", api_image="api:local", tools_image="tools:local"
    )
    assert 'image: "tools:local"' in config
    assert "TEASSISTANT_TOOLS_URL: http://tools:8001" in config
    assert "condition: service_healthy" in config
    assert "TOOL_WORK_ROOT: /app/work" in config
    assert "ports:" not in config and "internal: true" in config


def test_tools_workflow_compares_published_manifest_with_verified_staging_map():
    workflow = (ROOT / ".github/workflows/tools.yml").read_text(encoding="utf-8")
    stage = workflow.split("- name: Stage tested image for trusted publication", 1)[1].split(
        "  publish:\n", 1
    )[0]
    assert 'docker push "ghcr.io/contr4l/teassistant-tools:$STAGE_TAG"' in stage
    assert "manifest inspect" not in stage
    publish = workflow.split("- name: Publish only after verifying both architecture descriptors", 1)[1]
    assert "docker manifest create \"$IMAGE_REF\" \"$STAGE_AMD64\" \"$STAGE_ARM64\"" in publish
    assert 'docker manifest push --purge "$IMAGE_REF"' in publish
    assert publish.index('docker manifest push --purge "$IMAGE_REF"') < publish.index(
        'docker manifest inspect "$IMAGE_REF" > "$manifest"',
        publish.index('docker manifest push --purge "$IMAGE_REF"'),
    )
    assert 'cmp "$expected" "$verified"' in publish
    assert 'if docker manifest inspect "$IMAGE_REF" > "$manifest" 2>"$manifest_error"; then' in publish
    assert 'python3 scripts/verify_image_manifest.py --input "$manifest" > "$verified"' in publish
