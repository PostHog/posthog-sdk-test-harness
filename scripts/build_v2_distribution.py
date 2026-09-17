"""Build wheel/sdist in isolation with a verified sdk-specs snapshot; never publish."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from posthog_test_harness.v2.bundle import FORMAT, MANIFEST, identity, validate_bundle
from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.discovery import discover, feature_paths
from posthog_test_harness.v2.migration import migration_paths


def source_state(root):
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)),
    }


def bundle_paths(specs):
    # Runtime features, data and audit ledgers. Historical YAML and generator toolchains
    # are source provenance, not executable inputs of this distribution.
    patterns = [
        "acceptance/**/*.feature",
        "acceptance/**/*.json",
        *[
            f"contracts/v2/{directory}/*.{suffix}"
            for directory in ("inputs", "generated")
            for suffix in ("json", "md", "ts")
        ],
        "contracts/v2/*.md",
        "contracts/v2/protocol.ts",
        *[f"migration/yaml-parity-v1/*.{suffix}" for suffix in ("feature", "json", "md")],
        "coverage/harness-v2/*.json",
        "coverage/harness-v2/*.jsonl",
    ]
    return sorted({p.relative_to(specs).as_posix() for pattern in patterns for p in specs.glob(pattern) if p.is_file()})


def create_bundle(specs, destination, *, allow_dirty=False):
    specs = Path(specs).resolve()
    before = source_state(specs)
    if before["dirty"] and not allow_dirty:
        raise ValueError("Specs checkout is dirty; --allow-dirty creates a development snapshot only")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    digests = {}
    for name in bundle_paths(specs):
        source = specs / name
        if source.is_symlink() or not source.resolve().is_relative_to(specs):
            raise ValueError("Bundle input escapes the specs checkout")
        data = source.read_bytes()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digests[name] = hashlib.sha256(data).hexdigest()
    if source_state(specs) != before or any(
        hashlib.sha256((specs / n).read_bytes()).hexdigest() != h for n, h in digests.items()
    ):
        raise ValueError("Specs changed during snapshot construction")
    catalog = Contracts(destination / "contracts/v2")
    canonical = discover(destination, feature_paths(destination))
    migrated = discover(destination, migration_paths(destination))
    if len(canonical["cases"]) != 728 or len(migrated["cases"]) != 157:
        raise ValueError("Frozen inventory counts differ")
    if any(c["status"] != "harness_ready" for c in migrated["cases"]):
        raise ValueError("Migration suite has missing harness steps")
    manifest = {
        "format": FORMAT,
        "source": before,
        "catalog_sha256": catalog.catalog_hash,
        "scopes": {
            "canonical": {"cases": 728, "purpose": "discovery"},
            "yaml-parity-v1": {"cases": 157, "purpose": "migration execution"},
        },
        "files": digests,
    }
    manifest["bundle_sha256"] = identity(manifest)
    (destination / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    validate_bundle(destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--allow-dirty", action="store_true", help="Mark uncommitted inputs as development snapshots")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    runner = source_state(repo)
    if runner["dirty"] and not args.allow_dirty:
        parser.error("Harness checkout is dirty; --allow-dirty creates development artifacts only")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (
        any(output.glob("*.whl"))
        or any(output.glob("*.tar.gz"))
        or any((output / n).exists() for n in ("distribution.json", "requirements.lock"))
    ):
        parser.error("Output already contains distribution artifacts; choose a fresh directory")
    with tempfile.TemporaryDirectory(prefix="posthog-v2-build-") as temporary:
        stage = Path(temporary) / "harness"
        stage.mkdir()
        for name in ("pyproject.toml", "MANIFEST.in", "README.md", "LICENSE", "CONTRACT.yaml", "uv.lock"):
            shutil.copy2(repo / name, stage / name)
        for name in ("src", "contracts"):
            shutil.copytree(
                repo / name, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
            )
        runner_files = {
            p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in stage.rglob("*")
            if p.is_file()
        }
        if source_state(repo) != runner or any(
            hashlib.sha256((repo / n).read_bytes()).hexdigest() != h for n, h in runner_files.items()
        ):
            raise ValueError("Harness changed during snapshot construction")
        bundle = create_bundle(args.specs, stage / "src/posthog_test_harness/v2/_bundle", allow_dirty=args.allow_dirty)
        provenance = {
            "runner": runner,
            "runner_snapshot_sha256": hashlib.sha256(json.dumps(runner_files, sort_keys=True).encode()).hexdigest(),
            "specs": bundle["source"],
            "bundle_sha256": bundle["bundle_sha256"],
            "development": runner["dirty"] or bundle["source"]["dirty"],
        }
        (stage / "src/posthog_test_harness/v2/build-provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n"
        )
        epoch = subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=repo, text=True).strip()
        subprocess.run(
            [
                "uv",
                "export",
                "--project",
                str(stage),
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-header",
                "--format",
                "requirements-txt",
                "--output-file",
                str(output / "requirements.lock"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["uv", "build", "--out-dir", str(output), str(stage)],
            check=True,
            env={**os.environ, "SOURCE_DATE_EPOCH": epoch},
        )
        artifacts = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir()
            if p.is_file() and (p.suffix == ".whl" or p.name.endswith(".tar.gz") or p.name == "requirements.lock")
        }
        (output / "distribution.json").write_text(
            json.dumps({**provenance, "artifacts": artifacts}, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps({"output": str(output), **provenance}, indent=2))


if __name__ == "__main__":
    main()
