"""Original shared-probe cases and deliberate identity/coordinator defects."""

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from posthog_test_harness.v2.concurrent_steps import STEPS as PREVIOUS_STEPS
from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.discovery import discover, feature_paths
from posthog_test_harness.v2.probe_steps import STEPS, held
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS
from tests.test_v2_local_flags import identity
from tests.test_v2_network_gates import service
from tests.v2_flush_host import serve
from tests.v2_probe_host import ProbeEngine, ProbeHost

IDS = [identity("evaluate-flags", line) for line in (270, 280)]
DISJOINT = identity("evaluate-flags", 336)


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


async def test_original_shared_and_disjoint_probes(contracts):
    async with serve(contracts, host_type=ProbeHost) as (host, url):
        report, diagnostics = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[*IDS, DISJOINT]
        )
    assert strict_exit_code(contracts, report) == 0, report["results"]
    assert sum(r["result"]["status"] == "passed" for r in report["results"]) == 3
    assert sum(r["result"]["status"] == "not_selected" for r in report["results"]) == 725
    current = {c["case_id"] for c in discover(SPECS, registry=STEPS)["cases"] if c["status"] == "harness_ready"}
    previous = {
        c["case_id"] for c in discover(SPECS, registry=PREVIOUS_STEPS)["cases"] if c["status"] == "harness_ready"
    }
    assert current - previous == set(IDS)
    for case_id, count in zip(IDS, (1, 2)):
        case = next(d for d in diagnostics["cases"] if d["case_id"] == case_id)
        assert len(case["flag_requests"][case["flag_window_start"] :]) == count
        group = host.groups[case["fixture_id"]]
        refs = [r["completion"]["outcome"]["value"] for r in group["receipts"].values()]
        assert refs[0] != refs[1]
        assert all(
            c["route"] in ("/setup", "/evaluate_flags", "/snapshot/keys", "/snapshot/get_flag")
            for c in case["invocations"]
        )
    assert all(f.engine is None and not f.retained for f in host.fixtures.values())


@pytest.mark.parametrize(
    "defect,case_id,codes",
    [
        ("uncoordinated_probes", IDS[0], {"probe_in_flight", "probe_not_shared", "flag_request_count"}),
        ("uncoordinated_probes", IDS[1], {"probe_in_flight", "probe_not_shared"}),
        ("share_positive_probe", IDS[1], {"flag_request_count"}),
        ("wrong_waiter_value", IDS[1], {"flag_value"}),
        ("accessor_reevaluates", IDS[1], {"snapshot_network"}),
        ("serialize_all_probes", DISJOINT, {"mock_arrival_timeout", "host_deadline"}),
    ],
)
async def test_defects_fail_and_leave_next_case_independent(contracts, defect, case_id, codes):
    following = identity("get-feature-flag", 41)
    async with serve(contracts, host_type=ProbeHost, defect=defect) as (host, url):
        report, _ = await run(
            contracts,
            SPECS,
            feature_paths(SPECS),
            url,
            host.profile["id"],
            case_ids=[case_id, following],
            timeout_ms=200,
        )
    results = {r["case_id"]: r["result"] for r in report["results"]}
    failed = results[case_id]
    assert failed["status"] in ("failed_assertion", "harness_error") and failed["failure"]["code"] in codes, failed
    assert (
        failed["failure"]["call_ids"]
        and failed["failure"]["failed_step"]["source"]["path"] == "acceptance/public/evaluate-flags.feature"
    )
    assert results[following]["status"] == "passed"
    assert strict_exit_code(contracts, report) == 1 and len(host.closed) == 2


async def test_already_completed_early_second_request_cannot_escape_in_flight_assertion(contracts):
    class CompletedSecondHost(ProbeHost):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.second_done = asyncio.Event()

        async def invoke(self, fixture, call):
            result = await super().invoke(fixture, call)
            if call["route"] == "/evaluate_flags" and call["args"]["distinct_id"] == "user-456":
                self.second_done.set()
            return result

    async with serve(contracts, host_type=CompletedSecondHost, defect="uncoordinated_probes") as (host, url):
        registry = deepcopy(STEPS)
        pattern = "exactly one remote feature flag evaluation request should be in flight"
        registry.definitions = [d for d in registry.definitions if d[0].pattern != pattern]

        @registry.step(pattern)
        async def observe_after_second_completion(ctx, step):
            await asyncio.wait_for(host.second_done.wait(), 2)
            await held(ctx, step)

        report, _ = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[IDS[1]], registry=registry
        )
    result = next(r["result"] for r in report["results"] if r["case_id"] == IDS[1])
    assert result["status"] == "failed_assertion" and result["failure"]["code"] == "probe_not_shared"
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("outcome", ["clean", "http_error", "evaluation_error", "false_value", "empty_variant"])
async def test_only_clean_omissions_are_shared_with_native_waiters(outcome):
    async with service() as (server, _):
        engine = ProbeEngine({}, "2025-01-01T00:00:00Z", server.url, None)
        engine.install({"flags": [], "cohorts": {}, "group_type_mapping": {}})
        first_values = (
            {"flag": False} if outcome == "false_value" else {"flag": ""} if outcome == "empty_variant" else {}
        )
        server.set_flags("first", first_values, {})
        server.set_flags("second", {"flag": True}, {})
        if outcome == "http_error":
            server.fail_next_flags(503)
        if outcome == "evaluation_error":
            with server.lock:
                server.flag_responses["first"]["errorsWhileComputingFlags"] = True
        server.gates.arm("first", distinct_id="first")
        first = asyncio.create_task(engine.evaluate({"distinct_id": "first", "flag_keys": ["flag"]}))
        await server.gates.in_flight(["first"])
        second = asyncio.create_task(engine.evaluate({"distinct_id": "second", "flag_keys": ["flag"]}))
        try:
            await asyncio.wait_for(engine.joined.wait(), 2)
            assert not first.done() and not second.done()
            server.gates.release("first")
            a, b = await asyncio.gather(first, second)
            assert a.values == first_values
            assert b.values == ({} if outcome == "clean" else {"flag": True})
            assert len(server.flag_requests()) == (1 if outcome == "clean" else 2)
            assert engine.probes == {}
            # No successful refresh lifecycle is claimed. A later unrelated call
            # can probe again; only waiters on this live probe shared its omission.
            await engine.evaluate({"distinct_id": "later", "flag_keys": ["flag"]})
            assert len(server.flag_requests()) == (2 if outcome == "clean" else 3)
        finally:
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)


async def test_cancelling_owner_and_waiter_disposes_native_probe_state():
    async with service() as (server, _):
        engine = ProbeEngine({}, "2025-01-01T00:00:00Z", server.url, None)
        engine.install({"flags": [], "cohorts": {}, "group_type_mapping": {}})
        server.gates.arm("first", distinct_id="first")
        owner = asyncio.create_task(engine.evaluate({"distinct_id": "first", "flag_keys": ["flag"]}))
        await server.gates.in_flight(["first"])
        waiter = asyncio.create_task(engine.evaluate({"distinct_id": "second", "flag_keys": ["flag"]}))
        try:
            await asyncio.wait_for(engine.joined.wait(), 2)
        finally:
            owner.cancel()
            waiter.cancel()
            outcomes = await asyncio.gather(owner, waiter, return_exceptions=True)
        assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes)
        assert engine.probes == {}


@pytest.mark.parametrize("defect,code", [(None, 0), ("uncoordinated_probes", 1)])
async def test_shared_probe_cli_outside_checkout(contracts, tmp_path, defect, code):
    path = tmp_path / "probes.json"
    async with serve(contracts, host_type=ProbeHost, defect=defect) as (host, url):
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("posthog-test-harness-v2")),
            "run",
            "--specs",
            str(SPECS),
            "--contracts",
            str(CONTRACT_PATH),
            "--all-features",
            "--case-id",
            IDS[1],
            "--adapter-url",
            url,
            "--profile",
            host.profile["id"],
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
