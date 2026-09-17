"""Run a bounded YAML baseline using archived v1 code and the unchanged Node adapter.

Use the harness's locked Python environment. --legacy-harness must be a git archive
of the pinned v1 harness; this script does not import the dirty v2 action helpers.
The consumer must contain locally packed posthog-node, @posthog/core and express.
"""

import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, replace
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-harness", required=True, type=Path)
    parser.add_argument("--node-repo", required=True, type=Path)
    parser.add_argument("--consumer", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--align-native-defaults", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.legacy_harness.resolve() / "src"))
    from werkzeug.serving import make_server

    from posthog_test_harness import actions
    from posthog_test_harness.contract import ContractExecutor
    from posthog_test_harness.mock_server import MockServer, MockServerState
    from posthog_test_harness.sdk_adapter import SDKAdapterClient
    from posthog_test_harness.tests import TestContext
    from posthog_test_harness.tests.suites import ContractTestSuite

    assert (
        Path(actions.__file__).resolve().is_relative_to(args.legacy_harness.resolve())
    )
    selection = json.loads(args.selection.read_text())
    if not selection or len({row["id"] for row in selection}) != len(selection):
        raise ValueError("Selection must contain unique cases and must not be empty")
    executor = ContractExecutor(str(args.legacy_harness / "CONTRACT.yaml"))
    consumer = args.consumer.resolve()
    versions = {
        name: json.loads(
            (consumer / "node_modules" / name / "package.json").read_text()
        )["version"]
        for name in ("posthog-node", "@posthog/core")
    }
    if versions != {"posthog-node": "5.52.4", "@posthog/core": "1.54.2"}:
        raise ValueError(
            "This calibration's native-default alignment is pinned to Node 5.52.4 / core 1.54.2"
        )
    # Recreate the adapter's historical relative layout around the installed
    # package. The adapter bytes and its SDK method calls remain unchanged.
    layout = consumer / "legacy-layout"
    (layout / "compliance").mkdir(parents=True, exist_ok=True)
    (layout / "packages").mkdir(exist_ok=True)
    adapter_bytes = (args.node_repo / "compliance/node/adapter.js").read_bytes()
    (layout / "compliance/adapter.js").write_bytes(adapter_bytes)
    package_link = layout / "packages/node"
    if not package_link.exists():
        package_link.symlink_to(
            consumer / "node_modules/posthog-node", target_is_directory=True
        )
    assert (package_link / "dist/entrypoints/index.node.js").resolve() == (
        consumer / "node_modules/posthog-node/dist/entrypoints/index.node.js"
    ).resolve()

    class RecordedClient(SDKAdapterClient):
        def __init__(self, url):
            super().__init__(url)
            self.calls = []

        async def init(self, config):
            result = await super().init(config)
            self.calls.append(
                {"route": "/init", "args": asdict(config), "result": result}
            )
            return result

        async def capture(self, event):
            result = await super().capture(event)
            self.calls.append(
                {"route": "/capture", "args": asdict(event), "result": result}
            )
            return result

        async def capture_ai(self, event):
            result = await super().capture_ai(event)
            self.calls.append(
                {"route": "/capture_ai", "args": asdict(event), "result": result}
            )
            return result

        async def flush(self):
            result = await super().flush()
            self.calls.append({"route": "/flush", "args": {}, "result": result})
            return result

        async def get_feature_flag(self, request):
            result = await super().get_feature_flag(request)
            self.calls.append(
                {
                    "route": "/get_feature_flag",
                    "args": asdict(request),
                    "result": result,
                }
            )
            return result

    class AlignedClient(RecordedClient):
        async def init(self, config):
            # Values verified at the pinned SDK/core source. These neutralize
            # defaults inserted by v1, not defaults supplied by the v2 binding.
            config = replace(
                config,
                flush_at=20 if config.flush_at is None else config.flush_at,
                flush_interval_ms=(
                    5000
                    if config.flush_interval_ms is None
                    else config.flush_interval_ms
                ),
                disable_geoip=(
                    True if config.disable_geoip is None else config.disable_geoip
                ),
            )
            return await super().init(config)

    async def execute():
        results = []
        for row in selection:
            _, _, suite_name, category, name = row["legacy_id"].split(":")
            suite = ContractTestSuite(suite_name, executor)
            found = [
                (n, d)
                for n, d in suite.collect_tests(
                    "server", ["capture_v0", "capture_ai_v0", "encoding_gzip"]
                )
                if n == category + "." + name
            ]
            assert len(found) == 1, row["legacy_id"]
            state = MockServerState()
            server = make_server("127.0.0.1", 0, MockServer(state).app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            with tempfile.TemporaryFile() as log:
                process = subprocess.Popen(
                    ["node", str(layout / "compliance/adapter.js")],
                    cwd=consumer,
                    env={**os.environ, "PORT": str(port), "POSTHOG_CAPTURE_MODE": "v0"},
                    stdout=log,
                    stderr=log,
                )
                client_class = (
                    AlignedClient if args.align_native_defaults else RecordedClient
                )
                client = client_class(f"http://127.0.0.1:{port}")
                try:
                    await client.wait_for_health(timeout_seconds=10)
                    context = TestContext(
                        sdk_adapter=client,
                        mock_server=state,
                        mock_server_url=f"http://127.0.0.1:{server.server_port}",
                    )
                    result = await suite.run_single_test(*found[0], context)
                    results.append(
                        {
                            "case_id": row["id"],
                            "legacy_id": row["legacy_id"],
                            "result": asdict(result),
                            "calls": client.calls,
                            "wire_requests": [
                                {
                                    key: getattr(r, key)
                                    for key in (
                                        "path",
                                        "query_params",
                                        "body_decompressed",
                                        "headers",
                                        "parsed_events",
                                        "response_status",
                                        "response_body",
                                    )
                                }
                                for r in state.get_requests()
                            ],
                        }
                    )
                finally:
                    process.kill()
                    process.wait(timeout=5)
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
        return results

    report = {
        "mode": (
            "native-defaults-aligned" if args.align_native_defaults else "unchanged-v1"
        ),
        "package_versions": versions,
        "adapter_sha256": hashlib.sha256(adapter_bytes).hexdigest(),
        "actions_sha256": hashlib.sha256(
            Path(actions.__file__).read_bytes()
        ).hexdigest(),
        "results": asyncio.run(execute()),
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        {
            "passed": sum(r["result"]["passed"] for r in report["results"]),
            "total": len(report["results"]),
        }
    )
    raise SystemExit(0 if all(r["result"]["passed"] for r in report["results"]) else 1)


if __name__ == "__main__":
    main()
