"""Distribution identities and packaged CLI defaults, independent of SDK conformance."""

import json
import shutil

import pytest
from click.testing import CliRunner

from posthog_test_harness.v2 import bundle as resources
from posthog_test_harness.v2.bundle import MANIFEST, validate_bundle
from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.contracts import BoundaryError
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.migration import migration_paths
from scripts.build_v2_distribution import create_bundle
from tests.test_v2_gherkin import SPECS


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    path = tmp_path_factory.mktemp("distribution") / "_bundle"
    create_bundle(SPECS, path, allow_dirty=True)
    return path


def test_bundle_preserves_both_case_inventories_and_source_bytes(snapshot):
    manifest = validate_bundle(snapshot)
    assert type(manifest["source"]["dirty"]) is bool
    assert len(manifest["source"]["commit"]) == 40
    for paths, count in [(None, 885), (migration_paths(snapshot), 157)]:
        packaged = discover(snapshot, paths)
        source = discover(SPECS, paths)
        assert packaged == source
        assert len(packaged["cases"]) == count
    assert all(name.endswith(".feature") for name in manifest["files"])


def test_bundle_generation_is_deterministic_and_requires_dirty_opt_in(snapshot, tmp_path, monkeypatch):
    from scripts import build_v2_distribution as builder

    monkeypatch.setattr(builder, "source_state", lambda _: {"commit": "a" * 40, "dirty": True})
    with pytest.raises(ValueError, match="dirty"):
        create_bundle(SPECS, tmp_path / "rejected")
    first = create_bundle(SPECS, tmp_path / "first", allow_dirty=True)
    second = create_bundle(SPECS, tmp_path / "second", allow_dirty=True)
    assert first == second
    assert first["source"] == {"commit": "a" * 40, "dirty": True}


@pytest.mark.parametrize("mutation", ["feature", "missing", "extra", "manifest", "symlink"])
def test_bundle_rejects_incomplete_or_modified_resources(snapshot, tmp_path, mutation):
    root = tmp_path / "_bundle"
    shutil.copytree(snapshot, root)
    target = root / "migration/yaml-parity-v1/capture-ai.feature"
    if mutation == "feature":
        target = root / "migration/yaml-parity-v1/capture-ai.feature"
        target.write_text(target.read_text() + "\n")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (root / "unexpected.json").write_text("{}")
    elif mutation == "symlink":
        outside = tmp_path / "schema.json"
        shutil.copyfile(target, outside)
        target.unlink()
        target.symlink_to(outside)
    else:
        manifest = json.loads((root / MANIFEST).read_text())
        manifest["source"]["commit"] = "f" * 40
        (root / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(BoundaryError):
        validate_bundle(root)


def test_cli_uses_packaged_inputs_and_keeps_explicit_override(snapshot, tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "files", lambda _: snapshot.parent)
    runner = CliRunner()
    report = tmp_path / "report.json"
    args = ["discover", "--migration-suite", "--require-ready", "--report", str(report)]
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    data = json.loads(report.read_text())
    assert len(data["cases"]) == 157
    assert data["distribution"]["bundle_sha256"] == validate_bundle(snapshot)["bundle_sha256"]
    info = runner.invoke(main, ["bundle-info"])
    assert info.exit_code == 0, info.output
    assert json.loads(info.output) == validate_bundle(snapshot)
    result = runner.invoke(main, [*args, "--specs", str(SPECS)])
    assert result.exit_code == 0, result.output
    assert json.loads(report.read_text())["distribution"]["mode"] == "explicit-local-inputs"
    assert json.loads(report.read_text())["distribution"]["runner_version"]
    monkeypatch.setattr(resources, "files", lambda _: tmp_path / "absent")
    result = runner.invoke(main, args)
    assert result.exit_code != 0 and "No packaged specs bundle" in result.output
    assert runner.invoke(main, [*args, "--specs", str(SPECS)]).exit_code == 0
