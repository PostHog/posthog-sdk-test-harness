"""Bounded real-Node smoke on a private Docker network; supplied images are never published.

The adapter image must run the pinned Node v2 host against its installed public SDK
and bundled contracts, accepting --capture-mode, --listen-host and --listen-port.
"""

import argparse
import json
import signal
import subprocess
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--adapter-image", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    prefix = "posthog-v2-smoke-" + uuid4().hex[:12]
    network, runner, adapter = (prefix + suffix for suffix in ("-net", "-runner", "-adapter"))
    commands, results, cleanup_errors = [], [], []

    def docker(*command, check=True, timeout=240):
        process = subprocess.run(["docker", *command], capture_output=True, text=True, timeout=timeout)
        commands.append({"command": ["docker", *command], "exit": process.returncode})
        if check and process.returncode:
            raise RuntimeError(f"docker {command[0]} exited {process.returncode}: {process.stderr}")
        return process

    def cleanup(*command):
        try:
            docker(*command, timeout=30)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            cleanup_errors.append(str(error))

    def inspect(name):
        return json.loads(docker("inspect", name).stdout)[0]

    def stop_on_signal(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        docker("network", "create", "--internal", network)
        network_info = json.loads(docker("network", "inspect", network).stdout)[0]
        assert network_info["Internal"] is True
        docker(
            "run",
            "-d",
            "--name",
            runner,
            "--network",
            network,
            "--network-alias",
            "runner",
            "--entrypoint",
            "python",
            args.runner_image,
            "-c",
            "import time; time.sleep(1800)",
        )
        runner_info = inspect(runner)
        assert not runner_info["HostConfig"]["PortBindings"]
        assert runner_info["HostConfig"]["NetworkMode"] == network
        cases = json.loads(
            docker(
                "exec",
                runner,
                "python",
                "-c",
                "\n".join(
                    [
                        "import json",
                        "from posthog_test_harness.v2.bundle import specification_inputs",
                        "with specification_inputs() as (root, _):",
                        " print((root / 'migration/yaml-parity-v1/cases.json').read_text())",
                    ]
                ),
            ).stdout
        )
        ai_ids = [case["id"] for case in cases if ":capture_ai:" in case["id"]]
        geoip_ids = [case["id"] for case in cases if case["id"].endswith(":disable_geoip_omitted_defaults_to_false")]
        assert len(ai_ids) == 5 and len(geoip_ids) == 1
        topology = {"network": network_info, "runner": runner_info, "adapters": {}}
        (args.out / "topology.json").write_text(json.dumps(topology, indent=2) + "\n")
        for mode, profile in (("v0", "node-legacy"), ("v1", "node-analytics-v1")):
            docker(
                "run",
                "-d",
                "--name",
                adapter,
                "--network",
                network,
                "--network-alias",
                "adapter",
                args.adapter_image,
                "--capture-mode",
                mode,
                "--listen-host",
                "0.0.0.0",
                "--listen-port",
                "8080",
            )
            try:
                # Readiness is control-plane TCP only: never invoke or repair SDK behavior.
                docker(
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
                adapter_info = inspect(adapter)
                assert not adapter_info["HostConfig"]["PortBindings"]
                assert adapter_info["HostConfig"]["NetworkMode"] == network
                assert adapter_info["NetworkSettings"]["SandboxKey"] != runner_info["NetworkSettings"]["SandboxKey"]
                dns = {}
                for container, peer in ((runner, "adapter"), (adapter, "runner")):
                    dns[peer] = json.loads(
                        docker(
                            "exec",
                            container,
                            "python",
                            "-c",
                            f"import socket,json; print(json.dumps(socket.gethostbyname_ex({peer!r})))",
                        ).stdout
                    )
                assert adapter_info["NetworkSettings"]["Networks"][network]["IPAddress"] in dns["adapter"][2]
                assert runner_info["NetworkSettings"]["Networks"][network]["IPAddress"] in dns["runner"][2]
                topology["adapters"][mode] = {"inspect": adapter_info, "dns": dns}
                (args.out / "topology.json").write_text(json.dumps(topology, indent=2) + "\n")
                scopes = [("ai", ai_ids, "runner", 0), ("geoip", geoip_ids, "runner", 1)]
                if mode == "v1":
                    scopes.append(("topology-negative", ai_ids[:1], "127.0.0.1", 1))
                for scope, ids, advertised, expected in scopes:
                    label = f"{mode}-{scope}"
                    report_path = f"/tmp/{label}.json"
                    command = [
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
                        advertised,
                        "--timeout-ms",
                        "15000" if scope == "topology-negative" else "60000",
                        "--report",
                        report_path,
                    ]
                    for identity in ids:
                        command.extend(["--case-id", identity])
                    process = docker(*command, check=False)
                    (args.out / f"{label}.log").write_text(process.stdout + process.stderr)
                    for suffix in ("", ".diagnostics.json"):
                        docker("cp", f"{runner}:{report_path}{suffix}", str(args.out / f"{label}.json{suffix}"))
                    assert process.returncode == expected, (label, process.stdout, process.stderr)
                    report = json.loads((args.out / f"{label}.json").read_text())
                    diagnostics = json.loads((args.out / f"{label}.json.diagnostics.json").read_text())
                    selected = [row for row in report["results"] if row["case_id"] in ids]
                    statuses = [row["result"]["status"] for row in selected]
                    assert len(selected) == len(ids)
                    assert diagnostics["distribution"]["mode"] == "packaged"
                    assert diagnostics["network_config"] == {
                        "mock_bind_host": "0.0.0.0",
                        "mock_advertised_host": advertised,
                        "allow_private_network": True,
                    }
                    urls = [case["mock_url"] for case in diagnostics["cases"]]
                    assert len(urls) == len(set(urls)) == len(ids)
                    assert all(url.startswith(f"http://{advertised}:") for url in urls)
                    if scope == "ai":
                        assert not report["errors"] and statuses == ["passed"] * 5
                        assert any(
                            entry["path"] == "/i/v0/ai/batch/" and entry["status"] == 200
                            for case in diagnostics["cases"]
                            for entry in case["network"]
                        )
                    elif scope == "geoip":
                        assert not report["errors"] and statuses == ["failed_assertion"]
                        assert selected[0]["result"]["failure"]["code"] == "flag_request_field"
                        assert any(case["ingestion"] for case in diagnostics["cases"])
                    else:
                        assert all(status != "passed" for status in statuses)
                        assert all(not case["network"] for case in diagnostics["cases"])
                    results.append(
                        {
                            "label": label,
                            "exit": process.returncode,
                            "statuses": statuses,
                            "mock_urls": urls,
                            "bundle_sha256": diagnostics["distribution"]["bundle_sha256"],
                            "catalog_sha256": report["catalog_sha256"],
                        }
                    )
                    print(json.dumps(results[-1]), flush=True)
            finally:
                try:
                    logs = docker("logs", adapter, check=False, timeout=15)
                    (args.out / f"{mode}-host.log").write_text(logs.stdout + logs.stderr)
                finally:
                    cleanup("rm", "-f", adapter)
    finally:
        # Names are unique to this invocation; no global Docker state is modified.
        for name in (adapter, runner):
            cleanup("rm", "-f", name)
        cleanup("network", "rm", network)
        (args.out / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        (args.out / "cleanup-errors.json").write_text(json.dumps(cleanup_errors, indent=2) + "\n")
        if cleanup_errors:
            raise RuntimeError("Docker cleanup incomplete; see cleanup-errors.json")


if __name__ == "__main__":
    main()
