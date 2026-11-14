"""CI wiring checks for the lean API image and credential-free smoke gate."""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
TEXT = WORKFLOW.read_text()


def _job(name: str) -> str:
    marker = f"  {name}:\n"
    start = TEXT.index(marker)
    match = re.search(r"\n  [a-z][a-z0-9-]*:\n", TEXT[start + len(marker) :])
    next_job = start + len(marker) + match.start() if match else -1
    return TEXT[start : next_job if next_job >= 0 else len(TEXT)]


def test_ci_builds_and_size_gates_native_api_images_for_both_architectures():
    for job_name, tag in (("image", "teassistant-api-ci"), ("publish-platform", "teassistant-api-release")):
        job = _job(job_name)
        assert "target: api" in job
        assert f"tags: {tag}:${{{{ github.sha }}}}-${{{{ matrix.arch }}}}" in job
        assert "scope=api-${{ matrix.arch }}" in job
        assert "--max-bytes 200000000" in job
        assert "docker image inspect --format '{{.Architecture}}' \"$API_IMAGE_TAG\"" in job


def test_full_worker_and_lean_api_are_used_in_the_mixed_container_smoke():
    for job_name, full_tag, api_tag in (
        ("image", "teassistant-ci", "teassistant-api-ci"),
        ("publish-platform", "teassistant-release", "teassistant-api-release"),
    ):
        job = _job(job_name)
        assert f"IMAGE_TAG: {full_tag}:${{{{ github.sha }}}}-${{{{ matrix.arch }}}}" in job
        assert f"API_IMAGE_TAG: {api_tag}:${{{{ github.sha }}}}-${{{{ matrix.arch }}}}" in job
        assert "container_smoke.py --image \"$IMAGE_TAG\" --api-image \"$API_IMAGE_TAG\"" in job


def test_publish_keeps_credentials_out_of_build_and_smoke_and_logs_in_after_gates():
    job = _job("publish-platform")
    login = job.index("Log in to GHCR after checks and smoke passed")
    assert job.index("Repeat isolated synthetic smoke before registry login") < login
    assert job.index("Enforce trusted lean API image size before registry login") < login
    assert "GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in job
    build_and_smoke = job[:login]
    assert "secrets." not in build_and_smoke
    assert "DEEPSEEK_API_KEY" not in job


def test_release_preserves_full_tags_and_stages_api_separately_with_manifest_gates():
    platform = _job("publish-platform")
    publisher = _job("publish")
    assert 'stage_tag=staging-${RUN_ID}-${RUN_ATTEMPT}-${ARCH}' in platform
    assert 'api_stage_tag=api-staging-${RUN_ID}-${RUN_ATTEMPT}-${ARCH}' in platform
    assert 'echo "stage_amd64=staging-${RUN_ID}-${RUN_ATTEMPT}-amd64"' in publisher
    assert 'echo "api_stage_amd64=api-staging-${RUN_ID}-${RUN_ATTEMPT}-amd64"' in publisher
    assert 'sha_tag="$FINAL_SHA"' in publisher
    assert 'sha_tag="api-$FINAL_SHA"' in publisher
    assert 'release_tag="api-$RELEASE_TAG"' in publisher
    assert publisher.index("Create and verify full and API release manifests before any final push") < publisher.index(
        "Push all preverified SHA and release manifests"
    )
    assert 'docker manifest push --purge "$GHCR_IMAGE:$FINAL_SHA"' in publisher
    assert 'docker manifest push --purge "$GHCR_IMAGE:api-$FINAL_SHA"' in publisher
    assert 'TEASSISTANT_IMAGE=$GHCR_IMAGE:$FINAL_SHA' in publisher
    assert 'TEASSISTANT_API_IMAGE=$GHCR_IMAGE:api-$FINAL_SHA' in publisher
    remote = publisher[
        publisher.index("Verify both remote published manifests") :
        publisher.index("Write deployment references")
    ]
    assert 'cmp "$sha_verified" "$release_verified"' in remote
    assert 'cmp "$sha_verified" "$RUNNER_TEMP/teassistant-$flavor-sha-platforms.json"' in remote


def test_platform_release_tag_is_protected_and_strict_before_login_or_staging():
    platform = _job("publish-platform")
    validation = platform[
        platform.index("Validate trusted release reference and prepare unique staging tag") :
        platform.index("Log in to GHCR after checks and smoke passed")
    ]
    assert "EVENT_NAME: ${{ github.event_name }}" in validation
    assert "SOURCE_REF: ${{ github.ref }}" in validation
    assert "SOURCE_REF_PROTECTED: ${{ github.ref_protected }}" in validation
    assert '[[ "$SOURCE_REF_PROTECTED" == true ]]' in validation
    assert '[[ "$release_tag" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in validation
    assert validation.index('[[ "$release_tag" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]') < validation.index(
        'echo "stage_tag='
    )
