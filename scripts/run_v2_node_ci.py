"""Strict, opt-in Node source-build pilot. No SDK failure expectations are encoded here."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

COMMIT = r"[0-9a-f]{40}"
REPOSITORY = r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"
IMAGE = r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}"
PROFILES = (("v0", "node-legacy"), ("v1", "node-analytics-v1"))
LABEL = "posthog.v2.node-ci"


def validate_inputs(repository, revision, harness_revision, runner_image, node_image):
    for name, value, pattern in (
        ("SDK repository", repository, REPOSITORY),
        ("SDK revision", revision, COMMIT),
        ("harness revision", harness_revision, COMMIT),
        ("runner image", runner_image, IMAGE),
        ("Node image", node_image, IMAGE),
    ):
        if not re.fullmatch(pattern, value):
            raise ValueError(f"Invalid immutable {name}: {value!r}")


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class Commands:
    """Persist stdout/stderr and raw outcomes even for failed or timed-out commands."""

    def __init__(self, out):
        self.out = out
        self.path = out / "commands.json"
        self.records = json.loads(self.path.read_text()) if self.path.exists() else []

    def run(self, argv, timeout=60, check=True):
        log = self.out / f"command-{len(self.records):04d}.log"
        record = {"argv": [str(a) for a in argv], "log": log.name, "exit": None, "timed_out": False}
        self.records.append(record)
        save(self.path, self.records)
        with log.open("w") as stream:
            try:
                process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    record["exit"] = process.wait(timeout=timeout)
                except BaseException as error:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    record["timed_out"] = isinstance(error, subprocess.TimeoutExpired)
                    raise
            except (OSError, subprocess.TimeoutExpired) as error:
                record["error"] = str(error)
            finally:
                save(self.path, self.records)
        if check and record["exit"] != 0:
            raise RuntimeError(f"Command failed; see {log}")
        return record, log.read_text()

    def docker(self, *argv, **kwargs):
        return self.run(["docker", *argv], **kwargs)


def report_result(out, profile, cli):
    result = {"profile": profile, "cli_exit": cli["exit"], "timed_out": cli["timed_out"], "tests_passed": False}
    try:
        report = json.loads((out / f"{profile}.json").read_text())
        diagnostics = json.loads((out / f"{profile}.json.diagnostics.json").read_text())
        rows = report["results"]
        if not isinstance(rows, list) or not rows or not isinstance(report["errors"], list):
            raise ValueError("Empty or malformed report")
        if not report["run_id"] or diagnostics["run_id"] != report["run_id"]:
            raise ValueError("Report/diagnostics run identity mismatch")
        if diagnostics["distribution"]["mode"] != "packaged":
            raise ValueError("Expected packaged runner inputs")
        if any(row["profile_id"] != profile for row in rows):
            raise ValueError("Unexpected profile in report")
        counts = Counter(row["result"]["status"] for row in rows)
        result["counts"] = dict(counts)
        result["report_errors"] = report["errors"]
        result["tests_passed"] = (
            cli["exit"] == 0
            and not cli["timed_out"]
            and not report["errors"]
            and counts["passed"] > 0
            and all(
                row["result"]["status"] == "not_selected"
                or (row["result"]["status"] == "passed" and row["result"]["executed"] is True)
                for row in rows
            )
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["report_error"] = str(error)
    return result


def aggregate(results, errors):
    return (
        not errors
        and [r["profile"] for r in results] == [p for _, p in PROFILES]
        and all(r["tests_passed"] for r in results)
    )


def cleanup(out, commands):
    """Discover only this invocation's labeled resources, including partially started runs."""
    state = json.loads((out / "state.json").read_text())
    errors = []
    selector = f"label={LABEL}={state['owner']}"
    for kind, listing in (("container", ("ps", "-aq", "--no-trunc")), ("network", ("network", "ls", "-q"))):
        record, names = commands.docker(*listing, "--filter", selector, check=False)
        if record["exit"] != 0:
            errors.append(f"Could not list owned {kind}s")
            continue
        for name in names.splitlines():
            if kind == "container":
                # Retain logs and any partial reports before removal, including after cancellation.
                record, _ = commands.docker("logs", name, check=False)
                if record["exit"] != 0:
                    errors.append(f"Could not collect logs for {name}")
                for mode, profile in PROFILES:
                    if state.get("runners", {}).get(profile) == name:
                        errors.extend(collect(out, commands, name, profile))
                argv = ("rm", "-f", name)
            else:
                argv = ("network", "rm", name)
            record, _ = commands.docker(*argv, check=False)
            if record["exit"] != 0:
                errors.append(f"Could not remove owned {kind} {name}")
    save(out / "cleanup-errors.json", errors)
    return errors


def collect(out, commands, runner, profile):
    errors = []
    for suffix in (".json", ".json.diagnostics.json"):
        record, _ = commands.docker(
            "cp", f"{runner}:/tmp/{profile}{suffix}", str(out / f"{profile}{suffix}"), check=False
        )
        if record["exit"] != 0:
            errors.append(f"Could not collect {profile}{suffix}")
    return errors


def run_profiles(out, commands, runner_image, adapter_image, state):
    owner = state["owner"]
    network = owner + "-net"
    label = f"{LABEL}={owner}"
    commands.docker("network", "create", "--internal", "--label", label, network)
    _, raw = commands.docker("network", "inspect", network)
    if json.loads(raw)[0]["Internal"] is not True:
        raise RuntimeError("Docker network is not internal")
    results = []
    for mode, profile in PROFILES:
        runner, adapter = owner + "-runner-" + mode, owner + "-adapter-" + mode
        cli = {"exit": None, "timed_out": False}
        errors = []
        try:
            _, runner_id = commands.docker(
                "run",
                "-d",
                "--name",
                runner,
                "--label",
                label,
                "--network",
                network,
                "--network-alias",
                "runner",
                "--entrypoint",
                "python",
                runner_image,
                "-c",
                "import time; time.sleep(1800)",
            )
            state["runners"][profile] = runner_id.strip()
            save(out / "state.json", state)
            commands.docker(
                "run",
                "-d",
                "--name",
                adapter,
                "--label",
                label,
                "--network",
                network,
                "--network-alias",
                "adapter",
                adapter_image,
                "--capture-mode",
                mode,
                "--listen-host",
                "0.0.0.0",
                "--listen-port",
                "8080",
            )
            _, raw = commands.docker("inspect", runner, adapter)
            topology = json.loads(raw)
            save(out / f"{profile}-topology.json", topology)
            for container in topology:
                if container["HostConfig"]["PortBindings"] or container["HostConfig"]["NetworkMode"] != network:
                    raise RuntimeError("Unexpected ports or network mode")
            if topology[0]["NetworkSettings"]["SandboxKey"] == topology[1]["NetworkSettings"]["SandboxKey"]:
                raise RuntimeError("Containers share a network namespace")
            commands.docker(
                "exec",
                runner,
                "python",
                "-c",
                "\n".join(
                    [
                        "import socket,time",
                        "deadline=time.monotonic()+20",
                        "while True:",
                        " try:",
                        "  socket.create_connection(('adapter',8080),timeout=1).close(); break",
                        " except OSError:",
                        "  if time.monotonic() >= deadline: raise",
                        "  time.sleep(.1)",
                    ]
                ),
                timeout=30,
            )
            cli, _ = commands.docker(
                "exec",
                runner,
                "posthog-test-harness-v2",
                "run",
                "--migration-suite",
                "--adapter-url",
                "http://adapter:8080",
                "--allow-private-network",
                "--profile",
                profile,
                "--mock-bind-host",
                "0.0.0.0",
                "--mock-advertised-host",
                "runner",
                "--timeout-ms",
                "60000",
                "--report",
                f"/tmp/{profile}.json",
                timeout=900,
                check=False,
            )
        except (RuntimeError, OSError, ValueError) as error:
            errors.append(str(error))
        finally:
            errors.extend(collect(out, commands, runner, profile))
            record, _ = commands.docker("logs", adapter, check=False)
            if record["exit"] != 0:
                errors.append(f"Could not collect logs for {adapter}")
            # Stop each pair before reusing DNS aliases for the next profile.
            for name in (adapter, runner):
                record, _ = commands.docker("rm", "-f", name, check=False)
                if record["exit"] != 0:
                    errors.append(f"Could not remove {name}")
        result = report_result(out, profile, cli)
        result["infrastructure_errors"] = errors
        result["tests_passed"] = result["tests_passed"] and not errors
        results.append(result)
        save(out / "profiles.json", results)
    return results


def execute(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = Commands(out)
    state = {"owner": "posthog-v2-ci-" + uuid4().hex, "runners": {}}
    save(out / "state.json", state)
    results, errors = [], []
    try:
        if args.command == "build-run":
            validate_inputs(
                args.sdk_repository, args.sdk_revision, args.harness_revision, args.runner_image, args.node_image
            )
            save(out / "inputs.json", {k: str(v) for k, v in vars(args).items()})
            for root, expected in (
                (args.sdk_root, args.sdk_revision),
                (Path(__file__).resolve().parents[1], args.harness_revision),
            ):
                _, actual = commands.run(["git", "-C", str(root), "rev-parse", "HEAD"])
                if actual.strip() != expected:
                    raise ValueError("Checkout HEAD does not match requested revision")
            adapter_image = state["owner"] + ":build"
            commands.run(
                [
                    sys.executable,
                    str(args.sdk_root / "compliance/node/v2/build-packages.py"),
                    "--sdk-root",
                    str(args.sdk_root.resolve()),
                    "--out",
                    str(out / "build"),
                    "--node-image",
                    args.node_image,
                    "--runner-image",
                    args.runner_image,
                    "--adapter-image",
                    adapter_image,
                ],
                timeout=2400,
            )
        else:
            for image in (args.runner_image, args.adapter_image):
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                    raise ValueError("Local replay requires exact local image IDs")
            adapter_image = args.adapter_image
        results = run_profiles(out, commands, args.runner_image, adapter_image, state)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        errors.append(f"{type(error).__name__}: {error}")
    finally:
        try:
            errors.extend(cleanup(out, commands))
        except Exception as error:
            errors.append(f"Cleanup failed: {error}")
        passed = aggregate(results, errors)
        save(out / "summary.json", {"tests_passed": passed, "profiles": results, "errors": errors})
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    build = sub.add_parser("build-run")
    replay = sub.add_parser("replay", help="Local evidence only; uses immutable local image IDs, not CI inputs")
    clean = sub.add_parser("cleanup")
    for command in (validate, build):
        for name in ("sdk-repository", "sdk-revision", "harness-revision", "runner-image", "node-image"):
            command.add_argument("--" + name, required=True)
    build.add_argument("--sdk-root", required=True, type=Path)
    replay.add_argument("--runner-image", required=True)
    replay.add_argument("--adapter-image", required=True)
    for command in (build, replay, clean):
        command.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    if args.command == "validate":
        validate_inputs(
            args.sdk_repository, args.sdk_revision, args.harness_revision, args.runner_image, args.node_image
        )
        return 0
    if args.command == "cleanup":
        if not (args.out / "state.json").exists():
            return 0
        errors = cleanup(args.out, Commands(args.out))
        if errors:
            save(args.out / "summary.json", {"tests_passed": False, "errors": errors})
        return 1 if errors else 0
    return execute(args)


if __name__ == "__main__":
    sys.exit(main())
