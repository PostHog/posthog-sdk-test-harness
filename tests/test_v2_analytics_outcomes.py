"""Pinned analytics-v1 cases 70–98: 18 executable and 11 public-contract gaps."""

import hashlib
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from posthog_test_harness.types import MockResponse
from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.analytics_outcome_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, migration_manifest, selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import table
from tests.test_v2_analytics_batching import actions, expected_calls
from tests.test_v2_analytics_retry import expected_assertion as retry_assertion
from tests.test_v2_analytics_retry import observation, save_receipt
from tests.test_v2_analytics_wire import LEGACY, SOURCE
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_analytics_wire_host import AnalyticsWireEngine, AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = SUITE + "/capture-analytics-v1-outcomes.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]
SLICE = [row for row in LEGACY if row["source"]["path"] == SOURCE][69:98]
BLOCKED_NUMBERS = {83, 84, 85, 86, 88, 90, 91, 92, 93, 95, 96}
ORIGINS = [row for index, row in enumerate(SLICE, 70) if index not in BLOCKED_NUMBERS]
BLOCKERS = [
    row
    for row in json.loads((SPECS / SUITE / "blocked-cases.json").read_text())
    if row["legacy_source"]["path"] == SOURCE
]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def expected_assertion(action):
    name, params = action["action"], action.get("params", {})
    if name == "assert_partial_batch_retry_pruning":
        assert not params
        return ["the second request should retain first-response retry UUIDs and omit its terminal UUIDs"]
    if name == "assert_events_in_batch_count":
        assert params == {"expected": 1, "request_index": -1}
        return ["the last request should contain exactly 1 parsed events"]
    if name == "assert_header_absent":
        return [f'the first request header "{params["header"]}" should be absent']
    if name == "assert_event_option":
        assert params["absent"] is True
        return [f'the first received event option "{params["option"]}" should be absent']
    if name == "assert_body_field":
        assert params == {"field": "historical_migration", "expected": True}
        return ["the first request body should contain historical_migration equal to true"]
    if name == "assert_body_field_absent":
        return ["the first request body should omit these root fields:", params["fields"]]
    return [retry_assertion(action)]


def translated_calls(origin):
    calls = deepcopy(expected_calls(origin))
    for route, args in calls:
        if route == "/setup" and "enable_compression" in args["config"]:
            assert args["config"].pop("enable_compression") is False
            args["config"]["compression"] = "none"
    return calls


def test_exact_source_partition_inputs_filters_and_ordered_assertions():
    assert len(CASES) == len(ORIGINS) == 18
    assert len(BLOCKERS) == 11
    assert SLICE[0]["name"] == "handles_200_full_success"
    assert SLICE[-1]["name"] == "historical_migration_absent_by_default"
    for case, origin in zip(CASES, ORIGINS):
        row = case.migration
        assert row["legacy_id"] == origin["id"]
        assert row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert row["candidate_routes"] == ["/capture"] and row["sdk_capabilities"] == ["capture_v1"]
        assert row["native_sdk_evidence"] == []
        assert case.id == "migration:yaml-parity-v1:capture_analytics_v1:" + origin["name"]
        assert {"@both", "@api_capture_v1"} <= set(case.tags)
        assert len(case.steps) == len(actions(origin)) + 2
        for step, action in zip(case.steps[2:], actions(origin)):
            name, params = action["action"], action.get("params", {})
            if name == "configure_mock_responses":
                assert step.text == "the mock serves these ordered analytics responses:"
                actual = table(step, dict.fromkeys(("status", "headers", "body", "event_results"), "json"))
                expected = [
                    {
                        "status": r.get("status_code", 200),
                        "headers": r.get("headers", {}),
                        "body": r.get("body"),
                        "event_results": r.get("v1_event_results"),
                    }
                    for r in params["responses"]
                ]
                assert json_equal(actual, expected)
            elif name == "init":
                if "enable_compression" in params:
                    assert params == {"enable_compression": False, "flush_at": 1}
                    suffix = ", flush threshold 1, and compression disabled"
                elif "historical_migration" in params:
                    assert params == {"historical_migration": True, "flush_at": 1}
                    suffix = ", flush threshold 1, and historical migration enabled"
                else:
                    assert set(params) <= {"flush_at"}
                    suffix = " and " + (
                        f'flush threshold {params["flush_at"]}' if params else "no additional configuration"
                    )
                assert step.text == 'the SDK is initialized with token "phc_test_key"' + suffix
            elif name in ("capture", "capture_multiple"):
                expected_text = (
                    "capture is called with JSON arguments:"
                    if name == "capture"
                    else f'capture is called sequentially {params["count"]} times '
                    "with zero-based top-level index substitution:"
                )
                assert step.text == expected_text
                assert json_equal(json_arguments(step), params if name == "capture" else params["params"])
            elif name == "flush":
                assert step.text == "pending captures are flushed"
            elif name == "wait":
                assert step.text == f'{params["duration_ms"]} milliseconds elapse without a public SDK call'
            else:
                actual = [step.text]
                if "dataTable" in step.argument:
                    actual.append([r["cells"][0]["value"] for r in step.argument["dataTable"]["rows"][1:]])
                assert actual == expected_assertion(action)
    assert all(row["status"] == "harness_ready" for row in discover(SPECS, [FEATURE])["cases"])


def test_historical_blocker_ledger_preserves_exact_inputs_and_original_disposition(contracts):
    expected = [o for n, o in enumerate(SLICE, 70) if n in BLOCKED_NUMBERS]
    assert {b["legacy_id"] for b in BLOCKERS} | {c.migration["legacy_id"] for c in CASES} == {o["id"] for o in SLICE}
    assert not {b["id"] for b in BLOCKERS} & set(IDS)
    for row, origin in zip(BLOCKERS, expected):
        assert row["legacy_id"] == origin["id"] and row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert json_equal(row["source_inputs_and_ordered_assertions"], actions(origin))
        assert row["status"] == "blocked_contract" and row["translation"] == "not_translated"
        assert row["execution_evidence"] == row["native_sdk_evidence"] == []
        assert row["reason"] and row["unrepresentable_fields"] and row["proposed_seam_not_approved"]
        for path in row["unrepresentable_fields"]:
            record, field = path.split(".", 1)
            properties = contracts.schemas["catalog"]["definitions"][record]["properties"]
            if path != "SetupConfig.disable_geoip":
                assert field.split(".")[0] not in properties
    metadata = migration_manifest(SPECS)["blockers"]
    assert hashlib.sha256((SPECS / metadata["path"]).read_bytes()).hexdigest() == metadata["sha256"]
    assert SLICE[87 - 70]["name"] == "no_content_encoding_when_disabled"
    assert CASES[13].migration["argument_translations"] == [
        {
            "source": "init.enable_compression",
            "value": False,
            "target": "/setup.config.compression",
            "target_value": "none",
            "rationale": "Both explicitly disable compression without selecting an enabled algorithm.",
        }
    ]


async def test_all_18_execute_real_http_exact_public_inputs_and_observation_windows(contracts, tmp_path):
    async with serve(contracts, host_type=AnalyticsWireHost) as (host, url):
        started = time.monotonic()
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
        elapsed = time.monotonic() - started
    save_receipt(tmp_path, report, diagnostics)
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 18
    waits = sum(a.get("params", {}).get("duration_ms", 0) for o in ORIGINS for a in actions(o) if a["action"] == "wait")
    assert waits == 54000 and elapsed >= waits / 1000
    actual = []
    for row in host.inputs:
        call = deepcopy(row["invoke"])
        if call["route"] == "/setup":
            assert call["args"]["config"].pop("host").startswith("http://127.0.0.1:")
        actual.append([call["route"], call["args"]])
    assert json_equal(actual, [list(call) for origin in ORIGINS for call in translated_calls(origin)])
    assert len(report["calls"]) == 70
    assert len(host.closed) == 18 and all(f.engine is None and f.closed for f in host.fixtures.values())
    for case in diagnostics["cases"]:
        assert [r["response_status"] for r in case["wire_requests"]] == [r["status"] for r in case["network"]]
        assert all(r["path"] == "/i/v1/analytics/events" for r in case["wire_requests"])
    for index in range(4, 11):
        first, second = diagnostics["cases"][index]["wire_requests"]
        results = json.loads(first["response_body"])["results"]
        assert set(results) == {e["uuid"] for e in first["parsed_events"]}
        assert {e["uuid"] for e in second["parsed_events"]} == {k for k, v in results.items() if v["result"] == "retry"}
    unknown = json.loads(diagnostics["cases"][11]["wire_requests"][0]["response_body"])["results"]
    assert {"result": "unknown_future_result", "details": "some_new_detail"} in unknown.values()


@pytest.mark.parametrize(
    "defect,index,code",
    [
        ("duplicate_request", 0, "request_count"),
        ("retry_terminal_results", 2, "request_count"),
        ("retry_terminal_results", 3, "request_count"),
        ("retain_partial_terminals", 6, "partial_terminal_uuid"),
        ("discard_partial_retry", 4, "request_count"),
        ("retry_uuid", 7, "partial_missing_uuid"),
        ("frozen_attempt", 8, "retry_attempt"),
        ("replace_request_id", 9, "retry_request_id"),
        ("ignore_retry_after", 10, "retry_delay"),
        ("retry_terminal_results", 11, "request_count"),
        ("header:Content-Encoding:identity", 13, "header_present"),
        ("retry_terminal", 14, "terminal_request_count"),
        ("default_option:process_person_profile", 15, "event_option_present"),
        ("omit_body:historical_migration", 16, "historical_body"),
        ("root:historical_migration", 17, "body_field_present"),
    ],
)
async def test_distinct_outcome_families_reject_real_http_defects(contracts, tmp_path, defect, index, code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) == len(translated_calls(ORIGINS[index]))
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


async def test_partial_response_prunes_actual_buffer_before_next_flush():
    server = CaseServer()
    try:
        server.state.set_response_queue([MockResponse(v1_event_results=["retry", "limited", "retry", "ok"])])
        engine = AnalyticsWireEngine(
            {}, server.url, {"flush_at": 10, "max_retries": 0}, "phc_test_key", "analytics_v1", None
        )
        for name in ("first", "second", "third", "fourth"):
            await engine.capture({"distinct_id": "test_user", "event": name})
        before = deepcopy(engine.records)
        await engine.flush()
        assert engine.records == [before[0], before[2]]
        await engine.flush()
        assert not engine.records
        wire = server.state.get_requests()
        assert [r.parsed_events for r in wire] == [
            [r["event"] for r in before],
            [before[0]["event"], before[2]["event"]],
        ]
        assert set(json.loads(wire[0].response_body)["results"]) == {r["event"]["uuid"] for r in before}
    finally:
        server.close()


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "zstd"])
def test_blocked_encoding_requirements_remain_independent_features_not_fixture_or_runtime_gates(encoding):
    row = next(b for b in BLOCKERS if b["sdk_capabilities"] == ["capture_v1", "encoding_" + encoding])
    case = SimpleNamespace(migration=row)
    for runtime in ("server", "browser", "mobile", "edge"):
        profile = {
            "runtime": {"family": runtime},
            "sdk_capabilities": ["capture_v1"],
            "fixture_capabilities": ["encoding_" + encoding],
        }
        assert not selection(case, profile, ["/capture"])["selected"]
        explicit = selection(case, profile, [], explicit=True)
        assert explicit["selected"] and explicit["missing_routes"] == ["/capture"]
        assert explicit["missing_sdk_capabilities"] == ["encoding_" + encoding]
        profile["sdk_capabilities"].append("encoding_" + encoding)
        assert selection(case, profile, ["/capture"])["selected"]
        assert selection(case, profile, [])["selected"]  # API claim retains missing operation as a gap.
        profile["sdk_capabilities"] = ["encoding_" + encoding]
        assert not selection(case, profile, ["/capture"])["selected"]


@pytest.mark.parametrize(
    "options,explicit,status,code",
    [
        ({"sdk_capabilities": []}, False, "not_selected", None),
        ({"missing_route": "/capture"}, False, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/setup"}, False, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/flush"}, False, "unsupported_binding", "missing_operation"),
        ({"sdk_capabilities": []}, True, "unsupported_binding", "sdk_capability_unavailable"),
        ({"sdk_capabilities": [], "missing_route": "/capture"}, True, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, True, "blocked_fixture", "fixture_unavailable"),
    ],
)
async def test_outcome_preflight_preserves_prerequisite_gaps(contracts, options, explicit, status, code):
    async with serve(contracts, host_type=AnalyticsWireHost, **options) as (host, url):
        report, _ = await run(
            contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]] if explicit else []
        )
    selected_results = report["results"][:1] if explicit else report["results"]
    assert all(row["result"]["status"] == status for row in selected_results)
    if code:
        assert all(row["result"]["failure"]["code"] == code for row in selected_results)
    assert not host.fixtures and strict_exit_code(contracts, report) == 1


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


async def test_partial_pruning_uses_first_pair_and_preserves_weak_extra_and_duplicate_semantics():
    first = observation([{"uuid": "retry"}, {"uuid": "ok"}])
    first.response_body = '{"results":{"retry":{"result":"retry"},"ok":{"result":"ok"}}}'
    second = observation([{"uuid": "retry"}, {"uuid": "retry"}, {"uuid": "unknown"}])
    last = observation([{"uuid": "ok"}])
    text = "the second request should retain first-response retry UUIDs and omit its terminal UUIDs"
    await check(text, [first, second, last])
    await check("the last request should contain exactly 1 parsed events", [first, second, last])
    with pytest.raises(BoundaryError, match="count differs"):
        await check("the last request should contain exactly 1 parsed events", [first, second])
    second.parsed_events.append({"uuid": "ok"})
    with pytest.raises(BoundaryError) as error:
        await check(text, [first, second, last])
    assert error.value.code == "partial_terminal_uuid"
    second.parsed_events = []
    with pytest.raises(BoundaryError) as error:
        await check(text, [first, second, last])
    assert error.value.code == "partial_missing_uuid"


async def test_default_omissions_use_first_request_and_first_event_only():
    first, later = observation([{}, {"options": {"cookieless_mode": True}}]), observation()
    later.headers = {"content-encoding": "gzip"}
    await check('the first request header "Content-Encoding" should be absent', [first, later])
    first.headers["content-encoding"] = ""
    with pytest.raises(BoundaryError):
        await check('the first request header "Content-Encoding" should be absent', [first, later])
    for value in (None, {}, False):
        first.parsed_events[0]["options"] = value
        await check('the first received event option "cookieless_mode" should be absent', [first, later])
    first.parsed_events[0]["options"] = {"cookieless_mode": False}
    with pytest.raises(BoundaryError):
        await check('the first received event option "cookieless_mode" should be absent', [first, later])
    first.body_decompressed = '{"historical_migration":1}'
    await check("the first request body should contain historical_migration equal to true", [first, later])
    for body in ('{"batch":[{"historical_migration":true}]}', '{"historical_migration":false}', "not-json", ""):
        first.body_decompressed = body
        with pytest.raises(BoundaryError):
            await check("the first request body should contain historical_migration equal to true", [first, later])


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("default_option:product_tour_id", 1)])
async def test_outcomes_cli_outside_checkout_and_next_case_isolation(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect, runtime="browser") as (host, url):
        host.profile["products"] = ["flags"]
        for case in CASES:
            assert selection(case, host.profile, host.routes)["selected"]
        code, report, _, output = await cli_run(
            tmp_path,
            url,
            "--feature",
            FEATURE,
            "--profile",
            host.profile["id"],
            "--case-id",
            IDS[15],
            "--case-id",
            IDS[17],
        )
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert report["results"][17]["result"]["status"] == "passed"
    assert len(host.closed) == 2
