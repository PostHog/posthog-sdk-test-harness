"""Strict Node CI orchestration: pins, raw exits, evidence and owned cleanup."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_v2_node_ci as ci

PIN = "a" * 40
IMAGE = "ghcr.io/posthog/runner@sha256:" + "b" * 64


@pytest.mark.parametrize("image", [IMAGE, "node:24-bookworm-slim@sha256:" + "c" * 64])
def test_validate_inputs_accepts_digest_pins(image):
    ci.validate_inputs("PostHog/posthog-js", PIN, PIN, image, image)


@pytest.mark.parametrize(
    "field,value",
    [
        (0, "https://github.com/PostHog/posthog-js"),
        (0, "../node"),
        (0, "repo;touch /tmp/pwn"),
        (1, "main"),
        (1, "a" * 7),
        (1, "refs/pull/12/head"),
        (1, PIN + "\n"),
        (2, "v2"),
        (2, "$(echo bad)"),
        (3, "runner:latest"),
        (3, "runner:1.2.3"),
        (3, "sha256:" + "b" * 64),
        (3, IMAGE + "\n"),
        (3, IMAGE[:-1]),
        (4, "node:24"),
        (4, "--privileged"),
        (4, IMAGE + ";echo bad"),
    ],
)
def test_validate_inputs_rejects_floating_and_malformed_values(field, value):
    values = ["PostHog/posthog-js", PIN, PIN, IMAGE, IMAGE]
    values[field] = value
    with pytest.raises(ValueError):
        ci.validate_inputs(*values)


def write_report(out, profile="node-legacy", status="passed", errors=None):
    ci.save(
        out / f"{profile}.json",
        {
            "run_id": "run",
            "errors": errors or [],
            "results": [
                {"profile_id": profile, "result": {"status": status, "executed": True}},
                {"profile_id": profile, "result": {"status": "not_selected", "executed": False}},
            ],
        },
    )
    ci.save(out / f"{profile}.json.diagnostics.json", {"run_id": "run", "distribution": {"mode": "packaged"}})


@pytest.mark.parametrize(
    "exit_code,status,errors,expected",
    [
        (0, "passed", [], True),
        (1, "passed", [], False),
        (2, "passed", [], False),
        (None, "passed", [], False),
        (0, "failed_assertion", [], False),
        (1, "failed_assertion", [], False),
        (0, "passed", [{"code": "infrastructure"}], False),
        (0, "blocked_fixture", [], False),
        (0, "not_selected", [], False),
    ],
)
def test_report_result_keeps_cli_exit_and_rejects_report_failures(tmp_path, exit_code, status, errors, expected):
    write_report(tmp_path, status=status, errors=errors)
    result = ci.report_result(tmp_path, "node-legacy", {"exit": exit_code, "timed_out": False})
    assert result["cli_exit"] == exit_code
    assert result["tests_passed"] is expected


@pytest.mark.parametrize(
    "damage",
    ["missing-report", "missing-diagnostics", "invalid-json", "empty", "wrong-run", "wrong-profile", "timeout"],
)
def test_report_result_rejects_incomplete_evidence(tmp_path, damage):
    write_report(tmp_path)
    report = tmp_path / "node-legacy.json"
    diagnostics = tmp_path / "node-legacy.json.diagnostics.json"
    if damage == "missing-report":
        report.unlink()
    elif damage == "missing-diagnostics":
        diagnostics.unlink()
    elif damage == "invalid-json":
        report.write_text("{")
    elif damage == "empty":
        data = json.loads(report.read_text())
        data["results"] = []
        ci.save(report, data)
    elif damage == "wrong-run":
        ci.save(diagnostics, {"run_id": "other"})
    elif damage == "wrong-profile":
        data = json.loads(report.read_text())
        data["results"][0]["profile_id"] = "other"
        ci.save(report, data)
    assert not ci.report_result(tmp_path, "node-legacy", {"exit": 0, "timed_out": damage == "timeout"})["tests_passed"]


def test_aggregate_requires_both_profiles_and_no_infrastructure_errors():
    results = [{"profile": profile, "tests_passed": True} for _, profile in ci.PROFILES]
    assert ci.aggregate(results, [])
    assert not ci.aggregate(results[:1], [])
    assert not ci.aggregate([], [])
    assert not ci.aggregate(results, ["cleanup failed"])
    results[0]["tests_passed"] = False
    assert not ci.aggregate(results, [])


def test_commands_retain_failing_stdout_stderr_and_exit(tmp_path):
    commands = ci.Commands(tmp_path)
    record, log = commands.run(
        [sys.executable, "-c", "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)"], check=False
    )
    assert record["exit"] == 7
    assert "out" in log and "err" in log
    assert json.loads((tmp_path / "commands.json").read_text())[0] == record


def test_commands_timeout_retains_partial_log_without_manufacturing_cli_exit(tmp_path):
    record, log = ci.Commands(tmp_path).run(
        [
            sys.executable,
            "-u",
            "-c",
            "import time; print('started'); time.sleep(10)",
        ],
        timeout=0.2,
        check=False,
    )
    assert record["exit"] is None and record["timed_out"]
    assert "started" in log


def test_commands_missing_executable_is_failure_with_receipt(tmp_path):
    with pytest.raises(RuntimeError):
        ci.Commands(tmp_path).run([str(tmp_path / "missing")])
    assert json.loads((tmp_path / "commands.json").read_text())[0]["exit"] is None


class CleanupCommands:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "ps":
            return {"exit": 0}, "runner-id\nadapter-id\n"
        if args[:2] == ("network", "ls"):
            return {"exit": 0}, "network-id\n"
        return {"exit": 1 if self.fail else 0}, ""


def test_cleanup_collects_before_removal_and_attempts_all_owned_resources(tmp_path):
    ci.save(tmp_path / "state.json", {"owner": "owned", "runners": {"node-legacy": "runner-id"}})
    commands = CleanupCommands(fail=True)
    errors = ci.cleanup(tmp_path, commands)
    assert len(errors) == 7  # Two logs, two report copies and all three removals failed.
    calls = commands.calls
    assert calls[0][-1] == "label=posthog.v2.node-ci=owned"
    assert calls.index(("logs", "runner-id")) < calls.index(("rm", "-f", "runner-id"))
    assert any(call[0] == "cp" for call in calls)
    assert ("rm", "-f", "adapter-id") in calls
    assert ("network", "rm", "network-id") in calls
    assert json.loads((tmp_path / "cleanup-errors.json").read_text()) == errors


def test_build_failure_retains_log_and_cleans_up(tmp_path, monkeypatch):
    out = tmp_path / "output"
    calls = []

    def command(self, argv, **kwargs):
        if argv[0] == "git":
            return {"exit": 0}, PIN
        (out / "build-failed.log").write_text("build diagnostic")
        raise RuntimeError("build failed")

    monkeypatch.setattr(ci.Commands, "run", command)
    monkeypatch.setattr(ci, "cleanup", lambda *args: calls.append("cleanup") or [])
    args = SimpleNamespace(
        command="build-run",
        out=out,
        sdk_repository="PostHog/posthog-js",
        sdk_revision=PIN,
        harness_revision=PIN,
        runner_image=IMAGE,
        node_image=IMAGE,
        sdk_root=tmp_path,
    )
    assert ci.execute(args) == 1
    assert calls == ["cleanup"]
    assert (out / "build-failed.log").read_text() == "build diagnostic"
    assert not json.loads((out / "summary.json").read_text())["tests_passed"]


def test_unexpected_run_exception_still_cleans_up(tmp_path, monkeypatch):
    calls = []

    def fail(*args):
        raise RuntimeError("adapter failed")

    monkeypatch.setattr(ci, "run_profiles", fail)
    monkeypatch.setattr(ci, "cleanup", lambda *args: calls.append("cleanup") or ["cleanup failed"])
    args = SimpleNamespace(
        command="replay", out=tmp_path / "output", runner_image="sha256:" + "b" * 64, adapter_image="sha256:" + "c" * 64
    )
    assert ci.execute(args) == 1
    summary = json.loads((args.out / "summary.json").read_text())
    assert calls == ["cleanup"] and not summary["tests_passed"]
    assert "cleanup failed" in summary["errors"]


def test_workflow_is_opt_in_read_only_and_pinned():
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/test-sdk-v2-node.yml").read_text()
    )
    # PyYAML's YAML 1.1 loader reads the Actions `on` key as True.
    assert set(workflow[True]) == {"workflow_call"}
    assert workflow["permissions"] == {"contents": "read"}
    for step in workflow["jobs"]["node"]["steps"]:
        if "uses" in step:
            assert ci.re.fullmatch(ci.COMMIT, step["uses"].split("@")[1])
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
        if "run" in step:
            assert "${{" not in step["run"]
        assert "continue-on-error" not in step


@pytest.mark.parametrize(
    "cli_exit,status,logging_failure",
    [
        (0, "passed", False),
        (1, "failed_assertion", False),
        (0, "passed", True),
    ],
)
def test_run_profiles_executes_full_scope_and_keeps_both_raw_outcomes(tmp_path, cli_exit, status, logging_failure):
    state = {"owner": "owned", "runners": {}}

    class Docker:
        def __init__(self):
            self.calls = []

        def docker(self, *args, **kwargs):
            self.calls.append(args)
            record = {"exit": 0, "timed_out": False}
            if args[0] == "logs" and logging_failure:
                return {"exit": None, "timed_out": True}, "partial log"
            if args[:2] == ("network", "inspect"):
                return record, json.dumps([{"Internal": True}])
            if args[0] == "inspect":
                return record, json.dumps(
                    [
                        {
                            "HostConfig": {"PortBindings": {}, "NetworkMode": "owned-net"},
                            "NetworkSettings": {"SandboxKey": name},
                        }
                        for name in ("runner", "adapter")
                    ]
                )
            if args[0] == "exec" and "posthog-test-harness-v2" in args:
                assert "--migration-suite" in args and "--case-id" not in args
                assert "--feature" not in args
                profile = args[args.index("--profile") + 1]
                write_report(tmp_path, profile=profile, status=status)
                return {"exit": cli_exit, "timed_out": False}, "CLI output"
            return record, "id"

    commands = Docker()
    results = ci.run_profiles(tmp_path, commands, IMAGE, "local-adapter", state)
    assert [r["cli_exit"] for r in results] == [cli_exit, cli_exit]
    assert ci.aggregate(results, []) is (cli_exit == 0 and not logging_failure)
    assert [r["profile"] for r in results] == ["node-legacy", "node-analytics-v1"]
    assert len([c for c in commands.calls if c[0] == "rm"]) == 4
    assert len([c for c in commands.calls if c[0] == "cp"]) == 4
