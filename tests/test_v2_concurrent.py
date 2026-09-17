"""Concurrent native calls, individual receipts, deadlines and frozen-case evidence."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.concurrent import CAPABILITY, invoke_concurrent
from posthog_test_harness.v2.concurrent_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.discovery import discover, feature_paths
from posthog_test_harness.v2.fixtures import CaseServer, FlushControls
from posthog_test_harness.v2.local_flag_steps import STEPS as PREVIOUS_STEPS
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS
from tests.test_v2_local_flags import identity
from tests.v2_concurrent_host import ConcurrentHost
from tests.v2_flush_host import serve

CASE = identity("evaluate-flags", 336)


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


async def test_original_disjoint_probe_case(contracts):
    async with serve(contracts, host_type=ConcurrentHost) as (host, url):
        report, diagnostics = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[CASE]
        )
    assert strict_exit_code(contracts, report) == 0, report["errors"]
    assert sum(r["result"]["status"] == "passed" for r in report["results"]) == 1
    assert sum(r["result"]["status"] == "not_selected" for r in report["results"]) == 727
    current = {c["case_id"] for c in discover(SPECS, registry=STEPS)["cases"] if c["status"] == "harness_ready"}
    previous = {
        c["case_id"] for c in discover(SPECS, registry=PREVIOUS_STEPS)["cases"] if c["status"] == "harness_ready"
    }
    assert current - previous == {CASE}
    events = diagnostics["cases"][0]["network_gates"]
    arrivals = [e for e in events if e["transition"] == "arrived"]
    releases = [e for e in events if e["transition"] == "released"]
    assert len(arrivals) == len(releases) == 2
    assert max(e["at_ms"] for e in arrivals) <= min(e["at_ms"] for e in releases)
    calls = [c["invoke"] for c in host.inputs if c["invoke"]["route"] == "/evaluate_flags"]
    assert [c["args"] for c in calls] == [
        {"distinct_id": "user-123", "flag_keys": [key]} for key in ("missing-a", "missing-b")
    ]
    assert len({c["call_id"] for c in calls}) == 2
    assert all(f.engine is None and not f.retained for f in host.fixtures.values())


@pytest.mark.parametrize(
    "options,status",
    [
        ({"defect": "serial_group"}, "harness_error"),
        ({"defect": "concurrent_hang"}, "harness_error"),
        ({"defect": "wrong_group_attribution"}, "harness_error"),
        ({"defect": "missing_group_receipt"}, "harness_error"),
        ({"defect": "extra_group_receipt"}, "harness_error"),
        ({"defect": "reordered_group_receipts"}, "harness_error"),
        ({"missing_capability": CAPABILITY}, "blocked_fixture"),
        ({"missing_route": "/evaluate_flags"}, "unsupported_binding"),
    ],
)
async def test_invalid_groups_never_pass_and_do_not_contaminate_next_case(contracts, options, status):
    following = identity("get-feature-flag", 41)
    async with serve(contracts, host_type=ConcurrentHost, **options) as (host, url):
        report, _ = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[CASE, following], timeout_ms=100
        )
    results = {r["case_id"]: r["result"] for r in report["results"]}
    assert results[CASE]["status"] == status, results[CASE]
    assert results[CASE]["failure"]["failed_step"]["source"]["path"] == "acceptance/public/evaluate-flags.feature"
    assert results[following]["status"] == "passed", results[following]
    assert strict_exit_code(contracts, report) == 1
    assert all(job.done() for group in host.groups.values() for job in group["jobs"])
    assert len(host.closed) == 2


@asynccontextmanager
async def ready(contracts):
    server = CaseServer()
    try:
        async with serve(contracts, host_type=ConcurrentHost) as (host, url), Client(url, contracts) as client:
            async with client.fixture("group", "controlled", host.profile["id"], 1000) as fixture:
                controls = FlushControls(fixture, host.profile, 1000, [])
                await controls.command("scheduler_manual")
                await controls.command("clock_fixed", timestamp="2025-01-01T00:00:00Z")
                await controls.command("storage_empty")
                await fixture.invoke(
                    "setup", "/setup", {"project_token": "fixture-token", "config": {"host": server.url}}
                )
                yield host, client, fixture, server
    finally:
        await asyncio.to_thread(server.close)


def call(fixture, identity, route="/evaluate_flags", args=None):
    return {
        "call_id": identity,
        "route": route,
        "receiver": fixture.receiver,
        "args": {"distinct_id": "user-123", "flag_keys": [identity]} if args is None else args,
    }


async def test_duplicate_group_is_rejected_before_any_native_work(contracts):
    async with ready(contracts) as (host, client, fixture, server):
        invokes = [call(fixture, "same"), call(fixture, "same")]
        with pytest.raises(BoundaryError) as error:
            await invoke_concurrent(fixture, host.profile, invokes, 1000)
        assert error.value.code == "duplicate_id"
        assert set(client.calls) == {"setup"} and not client.pending and not fixture.busy
        assert host.groups == {}


@pytest.mark.parametrize("end", ["deadline", "cancel", "close"])
async def test_partial_completion_is_preserved_while_remaining_call_is_terminated(contracts, end):
    async with ready(contracts) as (host, client, fixture, server):
        server.gates.arm("held", flag_keys=["blocked"])
        invokes = [call(fixture, "ready", "/is_local_evaluation_ready", {}), call(fixture, "blocked")]
        task = asyncio.create_task(
            invoke_concurrent(fixture, host.profile, invokes, 150 if end == "deadline" else 1000)
        )
        await server.gates.in_flight(["held"])
        with pytest.raises(BoundaryError) as error:
            await fixture.invoke("not-entered", "/is_local_evaluation_ready", {})
        assert error.value.code == "invalid_state"
        if end == "cancel":
            response = await fixture.cancel("blocked")
            assert response["state"] == "cancelled"
        elif end == "close":
            await fixture.close()
        receipts = await task
        assert receipts[0]["completion"] == {"kind": "sdk", "outcome": {"kind": "value", "value": False}}
        assert receipts[1]["completion"]["failure"]["kind"] == ("timeout" if end == "deadline" else "cancelled")
        assert not fixture.active and not client.pending and not fixture.busy
        assert all(job.done() for job in host.groups[fixture.id]["jobs"])
        server.retire()


async def test_group_retains_each_actual_snapshot_for_subsequent_public_reads(contracts):
    async with ready(contracts) as (host, client, fixture, server):
        server.set_flags("user-123", {"a": True, "b": False}, {})
        receipts = await invoke_concurrent(fixture, host.profile, [call(fixture, "a"), call(fixture, "b")], 1000)
        refs = [r["completion"]["outcome"]["value"] for r in receipts]
        assert refs[0] != refs[1]
        for index, ref in enumerate(refs):
            result = await fixture.invoke(f"keys-{index}", "/snapshot/keys", {}, receiver=ref)
            assert result["completion"]["outcome"] == {"kind": "value", "value": [["a"], ["b"]][index]}
        assert set(client.calls) == {"setup", "a", "b", "keys-0", "keys-1"}


async def test_missing_group_binding_does_not_partially_invoke_supported_members(contracts):
    async with ready(contracts) as (host, client, fixture, server):
        client.negotiation["supported_routes"].remove("/evaluate_flags")
        invokes = [call(fixture, "ready", "/is_local_evaluation_ready", {}), call(fixture, "missing")]
        receipts = await invoke_concurrent(fixture, host.profile, invokes, 1000)
        assert [r["completion"]["failure"]["kind"] for r in receipts] == ["blocked_fixture", "unsupported_binding"]
        assert host.groups == {} and not fixture.busy and not client.pending
        assert len(host.inputs) == 1


@pytest.mark.parametrize("defect,code", [(None, 0), ("serial_group", 1)])
async def test_concurrent_case_cli_outside_checkout(contracts, tmp_path, defect, code):
    path = tmp_path / "concurrent.json"
    async with serve(contracts, host_type=ConcurrentHost, defect=defect) as (host, url):
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("posthog-test-harness-v2")),
            "run",
            "--specs",
            str(SPECS),
            "--contracts",
            str(CONTRACT_PATH),
            "--all-features",
            "--case-id",
            CASE,
            "--adapter-url",
            url,
            "--profile",
            host.profile["id"],
            "--timeout-ms",
            "200",
            "--report",
            str(path),
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        except BaseException:
            process.kill()
            await process.wait()
            raise
    assert process.returncode == code, (stdout, stderr)
    report = json.loads(path.read_text())
    assert len(report["results"]) == 728 and strict_exit_code(contracts, report) == code


@pytest.mark.parametrize("phase", ["barrier", "cleanup"])
async def test_runner_cancellation_closes_pending_native_group_without_waiting_for_deadline(contracts, phase):
    class SignalledHost(ConcurrentHost):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.entered = asyncio.Event()

        async def invoke(self, fixture, invocation):
            if invocation["route"] == "/evaluate_flags":
                self.entered.set()
                await asyncio.Event().wait()
            return await super().invoke(fixture, invocation)

    async with serve(contracts, host_type=SignalledHost) as (host, url):
        registry = deepcopy(STEPS)
        cleanup_started = asyncio.Event()
        if phase == "cleanup":
            pattern = (
                "two remote feature flag evaluation requests should be in flight before either response is released"
            )
            registry.definitions = [d for d in registry.definitions if d[0].pattern != pattern]

            @registry.step(pattern)
            async def fail_before_cleanup(ctx, step):
                await host.entered.wait()
                original_retire = ctx.server.retire

                def retire():
                    original_retire()
                    cleanup_started.set()

                ctx.server.retire = retire
                raise BoundaryError("controlled_assertion", "Force cleanup with pending calls", "failed_assertion")

        task = asyncio.create_task(
            run(
                contracts,
                SPECS,
                feature_paths(SPECS),
                url,
                host.profile["id"],
                case_ids=[CASE],
                timeout_ms=10000,
                registry=registry,
            )
        )
        try:
            await asyncio.wait_for((host.entered if phase == "barrier" else cleanup_started).wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert len(host.closed) == 1
        assert all(f.engine is None and not f.retained for f in host.fixtures.values())
        assert all(job.done() for group in host.groups.values() for job in group["jobs"])
        assert all(
            r["completion"]["failure"]["kind"] == "cancelled"
            for group in host.groups.values()
            for r in group["receipts"].values()
        )
