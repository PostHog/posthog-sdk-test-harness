"""Release wiring contracts; no registry or GitHub writes."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
RELEASE = WORKFLOW["jobs"]["release"]
STEPS = RELEASE["steps"]


def step(name):
    return next(item for item in STEPS if item["name"] == name)


def test_release_keeps_both_publishes_under_existing_approval_gate():
    assert RELEASE["environment"] == "Release"
    assert "needs.check-changesets.outputs.has-changesets == 'true'" in RELEASE["if"]
    publishes = [item for item in STEPS if item.get("with", {}).get("push") is True]
    assert [item["id"] for item in publishes] == ["publish-v1", "publish-v2"]
    for item in publishes:
        assert item["if"] == "steps.commit-release.outputs.commit-hash != ''"
        assert item["with"]["platforms"] == "linux/amd64,linux/arm64"
        assert STEPS.index(item) > STEPS.index(step("Smoke installed v2 distribution"))
        assert STEPS.index(item) > STEPS.index(step("Smoke v2 image"))
    assert publishes[0]["with"]["context"] == "."
    assert publishes[0]["with"]["file"] == "./Dockerfile"
    assert WORKFLOW["env"]["IMAGE_NAME"] == "posthog/sdk-test-harness"
    assert WORKFLOW["env"]["V2_IMAGE_NAME"] == "posthog/sdk-test-harness-v2"
    assert publishes[1]["with"]["context"] == "${{ runner.temp }}/v2-distribution"
    assert publishes[1]["with"]["file"] == "./Dockerfile.v2"


def test_validation_precedes_release_metadata_and_publishing():
    checks = [
        "Build v2 distribution",
        "Smoke installed v2 distribution",
        "Build v2 image for smoke",
        "Build arm64 v2 image for smoke",
        "Smoke v2 image",
    ]
    publications = [
        "Tag release",
        "Create GitHub Release",
        "Build and push Docker image",
        "Build and push v2 Docker image",
    ]
    for name in publications:
        publication = step(name)
        assert publication["if"] == "steps.commit-release.outputs.commit-hash != ''"
        assert all(STEPS.index(step(check)) < STEPS.index(publication) for check in checks)
    for arch, name in [("amd64", "Build v2 image for smoke"), ("arm64", "Build arm64 v2 image for smoke")]:
        build = step(name)
        assert build["with"]["platforms"] == f"linux/{arch}"
        assert build["with"]["tags"] == f"sdk-test-harness-v2:release-smoke-{arch}"
        assert build["with"]["load"] is True
        assert build["with"]["push"] is False
        assert STEPS.index(step("Set up QEMU")) < STEPS.index(build) < STEPS.index(step("Smoke v2 image"))
    assert step("Set up QEMU")["with"]["platforms"] == "arm64"


@pytest.mark.parametrize("failed_arch,expected_calls", [("", 6), ("amd64", 1), ("arm64", 4)])
def test_image_smoke_runs_both_platforms_and_propagates_failure(tmp_path, failed_arch, expected_calls):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_CALLS"\n'
        'if [[ -n "$FAILED_ARCH" && "$*" == *"linux/$FAILED_ARCH"* ]]; then exit 7; fi\n'
    )
    docker.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step("Smoke v2 image")["run"]],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "DOCKER_CALLS": str(calls),
            "FAILED_ARCH": failed_arch,
        },
    )
    assert result.returncode == (7 if failed_arch else 0)
    observed = calls.read_text().splitlines()
    assert len(observed) == expected_calls
    for index, call in enumerate(observed):
        arch = "amd64" if index < 3 else "arm64"
        assert f"--platform linux/{arch}" in call
        assert f"sdk-test-harness-v2:release-smoke-{arch}" in call
        if index % 3 == 0:
            assert call.endswith("bundle-info")
        else:
            assert f"{tmp_path}/v2-image-smoke/{arch}:/reports" in call
            suite = "migration" if index % 3 == 1 else "acceptance"
            selector = "--migration-suite" if suite == "migration" else "--acceptance-suite"
            report = "discovery.json" if suite == "migration" else "acceptance-discovery.json"
            assert f"discover {selector} --require-ready --report /reports/{report}" in call


@pytest.mark.parametrize("version,major,minor", [("0.9.3", "0", "0.9"), ("1.7.0", "1", "1.7")])
def test_release_images_share_sampo_tags(tmp_path, version, major, minor):
    output = tmp_path / "outputs"
    subprocess.run(
        ["bash", "-e", "-c", step("Compute Docker tags")["run"]],
        env={**os.environ, "NEW_VERSION": version, "GITHUB_OUTPUT": str(output)},
        check=True,
    )
    assert output.read_text().splitlines() == [f"exact={version}", f"major_minor={minor}", f"major={major}"]
    tags = step("Extract metadata")["with"]["tags"]
    assert step("Extract v2 metadata")["with"]["tags"] == tags
    assert tags.splitlines() == [
        "type=raw,value=${{ steps.docker-tags.outputs.exact }}",
        "type=raw,value=${{ steps.docker-tags.outputs.major_minor }}",
        "type=raw,value=${{ steps.docker-tags.outputs.major }},enable=${{ steps.docker-tags.outputs.major != '0' }}",
        "type=raw,value=latest",
    ]


def test_release_locks_version_before_commit_and_builds_clean_pinned_inputs():
    assert STEPS.index(step("Prepare release with Sampo")) < STEPS.index(step("Sync release lockfile"))
    assert step("Sync release lockfile")["run"] == "uv lock"
    assert STEPS.index(step("Sync release lockfile")) < STEPS.index(step("Commit release changes"))
    assert STEPS.index(step("Sync checkout to release commit")) < STEPS.index(step("Build v2 distribution"))
    pin = WORKFLOW["env"]["SDK_SPECS_COMMIT"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin)
    assert pin == "1d5fe4255c7990e8c2c09613c600fda590baf3cb"
    checkout = step("Checkout pinned SDK specs")["run"]
    assert 'fetch --depth=1 origin "$SDK_SPECS_COMMIT"' in checkout
    assert 'rev-parse HEAD)" = "$SDK_SPECS_COMMIT"' in checkout
    build = step("Build v2 distribution")["run"]
    assert "--locked" in build
    assert '--specs "$RUNNER_TEMP/sdk-specs" --out "$RUNNER_TEMP/v2-distribution"' in build
    assert "--allow-dirty" not in build
    smoke = step("Smoke installed v2 distribution")["run"]
    assert "--require-hashes" in smoke
    assert '"$RUNNER_TEMP"/v2-distribution/*.whl' in smoke
    assert "scripts/smoke_v2_distribution.py" in smoke


@pytest.mark.parametrize("v2_digest,outcome", [("sha256:222", "success"), ("", "failure")])
def test_release_handoff_preserves_each_image_result_on_partial_publish(tmp_path, v2_digest, outcome):
    record = step("Record image handoff")
    assert record["if"].startswith("always() && ")
    assert step("Upload release handoff artifacts")["if"] == record["if"]
    summary = tmp_path / "summary.md"
    subprocess.run(
        ["bash", "-e", "-c", record["run"]],
        env={
            **os.environ,
            **WORKFLOW["env"],
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RELEASE_VERSION": "1.7.0",
            "RELEASE_COMMIT": "a" * 40,
            "V1_DIGEST": "sha256:111",
            "V2_DIGEST": v2_digest,
            "V1_OUTCOME": "success",
            "V2_OUTCOME": outcome,
        },
        check=True,
    )
    handoff = json.loads((tmp_path / "release-images.json").read_text())
    assert handoff["version"] == "1.7.0"
    assert handoff["release_commit"] == "a" * 40
    assert handoff["specs_commit"] == WORKFLOW["env"]["SDK_SPECS_COMMIT"]
    assert handoff["images"]["v1"]["reference"] == "ghcr.io/posthog/sdk-test-harness@sha256:111"
    v2 = handoff["images"]["v2"]
    assert v2["digest"] == (v2_digest or None)
    assert v2["outcome"] == outcome
    assert v2["reference"] == (f"ghcr.io/posthog/sdk-test-harness-v2@{v2_digest}" if v2_digest else None)
    assert "sha256:111" in summary.read_text()
    artifacts = step("Upload release handoff artifacts")["with"]["path"].splitlines()
    assert artifacts == [
        "${{ runner.temp }}/release-images.json",
        "${{ runner.temp }}/v2-distribution/",
        "${{ runner.temp }}/v2-smoke/",
        "${{ runner.temp }}/v2-image-smoke/",
    ]
