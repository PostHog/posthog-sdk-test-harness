"""Remote snapshots: actual HTTP, retained references, strict reports and defects."""

import asyncio
import json
import sys
from pathlib import Path

import aiohttp
import pytest

from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.flag_steps import STEPS as SNAPSHOT_STEPS
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, PIN, SPECS, synthetic_specs
from tests.test_v2_gherkin import FEATURE as SYNTHETIC_FEATURE
from tests.v2_flags_host import FlagsHost
from tests.v2_flush_host import serve

FEATURE = "acceptance/public/evaluate-flags.feature"
LINES = (12, 25, 41, 54, 66, 77, 90, 107, 129)
IDS = [f"gherkin:{PIN[:7]}:{FEATURE}:L{line}" for line in LINES]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


async def test_existing_remote_snapshot_scenarios(contracts):
    async with serve(contracts, host_type=FlagsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=IDS)
    assert strict_exit_code(contracts, report) == 0, [
        r for r in report["results"] if r["result"]["status"] not in ("passed", "not_selected")
    ]
    assert {r["case_id"] for r in report["results"] if r["result"]["status"] == "passed"} == set(IDS)
    assert {
        c["case_id"]
        for c in discover(SPECS, [FEATURE], registry=SNAPSHOT_STEPS)["cases"]
        if c["status"] == "harness_ready"
    } == set(IDS)
    calls = [entry["invoke"] for entry in host.inputs]
    first = [c for c in host.inputs if c["fixture_id"] == diagnostics["cases"][0]["fixture_id"]]
    assert [c["invoke"]["route"] for c in first] == [
        "/setup",
        "/evaluate_flags",
        "/snapshot/is_enabled",
        "/snapshot/get_flag",
        "/snapshot/is_enabled",
    ]
    assert first[1]["invoke"]["args"] == {"distinct_id": "user-123"}
    assert len({c["invoke"]["receiver"]["id"] for c in first[2:]}) == 1
    captures = [c for c in calls if c["route"] == "/capture"]
    assert len(captures) == 2
    assert all(set(c["references"]) == {"/flags"} and "flags" not in c["args"] for c in captures)
    assert not any(c["route"] in ("/flush", "/get_feature_flag") for c in calls)
    first_route = next(c for c in discover(SPECS, [FEATURE])["cases"] if c["case_id"] == IDS[0])
    assert first_route["required_routes"] == ["/evaluate_flags", "/setup", "/snapshot/get_flag", "/snapshot/is_enabled"]
    assert len(host.closed) == 9
    assert all(not f.retained and not f.references and f.engine is None for f in host.fixtures.values())
    assert all(len([r for r in d["network"] if r["path"] == "/flags"]) == 2 for d in diagnostics["cases"])
    # Startup traffic is retained but not counted as request-time evaluation.
    assert all(d["flag_window_start"] == 1 for d in diagnostics["cases"])
    assert all(d["flag_requests"][0]["context"] == {} for d in diagnostics["cases"])
    assert diagnostics["cases"][-1]["flag_requests"][1]["context"] == {
        "distinct_id": "user-123",
        "flag_keys_to_evaluate": ["beta-ui", "checkout"],
    }
    assert "fixture-token" not in json.dumps(diagnostics)
    snapshot_receipts = [c for c in report["calls"] if c["route"] == "/evaluate_flags"]
    ids = {c["completion"]["outcome"]["value"]["id"] for c in snapshot_receipts}
    assert len(ids) == 9
    nulls = [
        c for c in report["calls"] if c["completion"] == {"kind": "sdk", "outcome": {"kind": "value", "value": None}}
    ]
    assert nulls and all(c["route"] == "/snapshot/get_flag" for c in nulls)


@pytest.mark.parametrize(
    "defect,line,code",
    [
        ("wrong_flag", 12, "flag_value"),
        ("first_enablement_wrong", 12, "flag_value"),
        ("wrong_flag_identity", 12, "flag_value"),
        ("accessor_reevaluates", 12, "flag_request_count"),
        ("eager_exposure", 54, "unexpected_event"),
        ("duplicate_exposure", 54, "flag_exposure_count"),
        ("wrong_payload", 66, "flag_payload"),
        ("payload_exposure", 66, "unexpected_event"),
        ("payload_marks_access", 66, "snapshot_keys"),
        ("capture_reevaluates", 77, "flag_request_count"),
        ("capture_wrong_flag", 77, "event_property"),
        ("broken_filter", 90, "snapshot_keys"),
        ("duplicate_keys", 25, "snapshot_keys"),
        ("omit_scope", 129, "flag_request_keys"),
        ("unscoped_snapshot", 129, "snapshot_keys"),
    ],
)
async def test_snapshot_defects_are_attributable_and_isolated(contracts, defect, line, code):
    # The earliest selected case owns the defect; a later, different case must pass.
    bad = f"gherkin:{PIN[:7]}:{FEATURE}:L{line}"
    later = next((identity for identity in IDS if int(identity.rsplit("L", 1)[1]) > line), None)
    selections = [bad] + ([later] if later else [])
    async with serve(contracts, host_type=FlagsHost, defect=defect) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=selections)
    assert strict_exit_code(contracts, report) == 1
    result = next(r["result"] for r in report["results"] if r["case_id"] == bad)
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code, result
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) >= 2
    if later:
        assert next(r["result"]["status"] for r in report["results"] if r["case_id"] == later) == "passed"
    assert len(host.closed) == len(selections)


@pytest.mark.parametrize(
    "options,status,code",
    [
        ({"missing_route": "/snapshot/get_flag"}, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "queue.snapshot.v1"}, "blocked_fixture", "missing_fixture"),
        ({"defect": "evaluation_timeout"}, "harness_error", "host_deadline"),
    ],
)
async def test_snapshot_infrastructure_outcomes(contracts, options, status, code):
    identity = IDS[1]  # Has both retained calls and capture queue observations.
    async with serve(contracts, host_type=FlagsHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[identity], timeout_ms=100)
    result = next(r["result"] for r in report["results"] if r["case_id"] == identity)
    assert result["status"] == status and result["failure"]["code"] == code, result
    assert strict_exit_code(contracts, report) == 1
    assert len(host.closed) == 1 and all(not f.retained for f in host.fixtures.values())


async def test_payload_outline_preserves_json_types(contracts, tmp_path):
    specs = synthetic_specs(
        tmp_path,
        """Feature: Payload types
 Background:
  Given a fresh SDK acceptance test harness
  And the SDK clock is fixed at "2025-01-01T00:00:00Z"
  And persistent storage is empty
  And the mock PostHog server is reset
  And the SDK is initialized with token "test-token"
 Scenario Outline: JSON payload
  Given remote feature flag evaluation for distinct id "user-123" returns:
   | key | value | payload |
   | f | variant | <payload> |
  When evaluate flags is called for distinct id "user-123"
  And snapshot payload is read for "f"
  Then the returned snapshot payload for "f" should be `<payload>`
  And no event named "$feature_flag_called" should be enqueued
  And snapshot only accessed should return no flags
  And exactly one remote feature flag evaluation request should have been sent
  Examples:
   | payload |
   | false |
   | 0 |
   | "" |
   | {} |
   | [] |
   | null |
""",
    )
    async with serve(contracts, host_type=FlagsHost) as (host, url):
        report, _ = await run(contracts, specs, [SYNTHETIC_FEATURE], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    payloads = [
        r["completion"]["outcome"]["value"] for r in report["calls"] if r["route"] == "/snapshot/get_flag_payload"
    ]
    assert [type(v) for v in payloads] == [bool, int, str, dict, list, type(None)]
    assert payloads == [False, 0, "", {}, [], None]
    assert all(":example-L" in r["case_id"] for r in report["results"])


async def test_flag_responses_do_not_consume_ingestion_failure_and_retired_hosts_reject():
    server, other = CaseServer(), CaseServer()
    try:
        server.set_flags("user-123", {"f": False}, {"f": 0})
        server.fail_next_ingestion(503)
        async with aiohttp.ClientSession() as session:
            async with session.post(server.url + "/flags", json={"distinct_id": "user-123"}) as response:
                body = await response.json()
                assert body["featureFlags"] == {"f": False}
                assert json.loads(body["featureFlagPayloads"]["f"]) == 0
            async with session.post(server.url + "/batch", json={"batch": [{"event": "E"}]}) as response:
                assert response.status == 503
            async with session.post(other.url + "/flags", json={"distinct_id": "user-123"}) as response:
                assert (await response.json())["featureFlags"] == {}
            server.retire()
            before = server.flag_requests()
            async with session.post(server.url + "/flags", json={"distinct_id": "user-123"}) as response:
                assert response.status == 410
            assert server.flag_requests() == before
            assert len(other.flag_requests()) == 1
    finally:
        await asyncio.to_thread(server.close)
        await asyncio.to_thread(other.close)


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("wrong_flag", 1)])
async def test_snapshot_cli_outside_checkout(contracts, tmp_path, defect, exit_code):
    path = tmp_path / "flags.json"
    async with serve(contracts, host_type=FlagsHost, defect=defect) as (host, url):
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("posthog-test-harness-v2")),
            "run",
            "--specs",
            str(SPECS),
            "--contracts",
            str(CONTRACT_PATH),
            "--feature",
            FEATURE,
            "--case-id",
            IDS[0],
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
    assert process.returncode == exit_code, (stdout, stderr)
    assert strict_exit_code(contracts, json.loads(path.read_text())) == exit_code
