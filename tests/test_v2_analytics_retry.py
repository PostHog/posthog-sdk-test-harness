"""Pinned analytics-v1 cases 41–69: source scopes, actual HTTP, and retry defects."""

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.analytics_retry_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import table
from tests.test_v2_analytics_batching import actions, expected_calls
from tests.test_v2_analytics_wire import LEGACY, SOURCE, assertion_texts
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = SUITE + "/capture-analytics-v1-retry.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]
ORIGINS = [row for row in LEGACY if row["source"]["path"] == SOURCE][40:69]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def expected_assertion(action):
    name, params = action["action"], action.get("params", {})
    fixed = {
        "assert_uuid_preserved_on_retry": 'the present event "uuid" lists in requests zero and one should be identical',
        "assert_timestamp_preserved_on_retry": (
            'the present event "timestamp" lists in requests zero and one should be identical'
        ),
        "assert_no_duplicate_events_in_batch": "every received batch should have no duplicate nonempty UUIDs",
        "assert_attempt_header_increments": (
            "all recorded request attempts should be consecutive integers starting at one"
        ),
        "assert_request_id_preserved_on_retry": "all recorded request IDs should equal the nonempty first request ID",
        "assert_different_request_ids": (
            'the first two request headers "posthog-request-id" should be nonempty and different'
        ),
        "assert_request_timestamp_changes_on_retry": (
            'the first two request headers "posthog-request-timestamp" should be nonempty and different'
        ),
        "assert_v1_response_has_results_map": "the first mock-authored response should contain a results object",
        "assert_v1_retry_after_present": "the first mock-authored response Retry-After should be present",
        "assert_v1_retry_after_absent": "the first mock-authored response Retry-After should be absent",
        "assert_v1_response_echoes_request_id": (
            "the first mock-authored response should echo the nonempty sent request ID"
        ),
        "assert_sdk_did_not_retry": (
            "exactly one recorded request excluding paths containing /flags should have been received"
        ),
    }
    if name in fixed:
        assert not params
        return fixed[name]
    if name == "assert_request_count_gte":
        return f'at least {params["expected"]} capture request should have been received'
    if name == "assert_v1_response_status":
        return f'the first mock-authored response should have status {params["expected"]}'
    if name == "assert_v1_all_events_result":
        return f'every first mock-authored response result should equal "{params["expected_result"]}"'
    if name == "assert_v1_response_results_count":
        return f'the first mock-authored response should contain exactly {params["expected"]} results'
    if name == "assert_final_success":
        assert params == {"success_statuses": [200]}
        return "at least one recorded response should have status 200"
    if name in ("assert_retry_delay", "assert_backoff_implemented"):
        minimum = params["min_delay_ms"] if name == "assert_retry_delay" else params["min_first_delay_ms"]
        return f"the first inter-request delay should be at least {minimum} milliseconds"
    return assertion_texts(action)[0]


def test_exact_29_source_cases_inputs_ordered_assertions_and_evidence_layers():
    assert len(CASES) == len(ORIGINS) == 29
    assert ORIGINS[0]["name"] == "preserves_uuid_on_retry"
    assert ORIGINS[-1]["name"] == "max_retries_respected"
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
        has_mock_check = False
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
                assert set(params) <= {"flush_at", "max_retries"}
                suffix = "no additional configuration"
                if "flush_at" in params:
                    suffix = f'flush threshold {params["flush_at"]}'
                if "max_retries" in params:
                    suffix = f'maximum retries {params["max_retries"]}'
                assert step.text == 'the SDK is initialized with token "phc_test_key" and ' + suffix
            elif name == "capture":
                assert step.text == "capture is called with JSON arguments:"
                assert json_equal(json_arguments(step), params)
            elif name == "capture_multiple":
                assert step.text == (
                    f'capture is called sequentially {params["count"]} times '
                    "with zero-based top-level index substitution:"
                )
                assert json_equal(json_arguments(step), params["params"])
            elif name == "flush":
                assert step.text == "pending captures are flushed"
            elif name == "wait":
                assert step.text == f'{params["duration_ms"]} milliseconds elapse without a public SDK call'
            else:
                assert step.text == expected_assertion(action)
                has_mock_check |= name.startswith("assert_v1_")
        assert ("mock_authored_response" in row["evidence_layers"]) == has_mock_check
    assert all(row["status"] == "harness_ready" for row in discover(SPECS, [FEATURE])["cases"])


def save_receipt(path, report, diagnostics):
    (path / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (path / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")


async def test_all_29_execute_exact_public_calls_real_windows_and_associated_responses(contracts, tmp_path):
    async with serve(contracts, host_type=AnalyticsWireHost) as (host, url):
        started = time.monotonic()
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
        elapsed = time.monotonic() - started
    save_receipt(tmp_path, report, diagnostics)
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 29
    waits = sum(a.get("params", {}).get("duration_ms", 0) for o in ORIGINS for a in actions(o) if a["action"] == "wait")
    assert waits == 109000 and elapsed >= waits / 1000
    actual = []
    for row in host.inputs:
        call = deepcopy(row["invoke"])
        if call["route"] == "/setup":
            assert call["args"]["config"].pop("host").startswith("http://127.0.0.1:")
        actual.append([call["route"], call["args"]])
    assert json_equal(actual, [list(call) for origin in ORIGINS for call in expected_calls(origin)])
    assert len(report["calls"]) == sum(len(expected_calls(o)) for o in ORIGINS)
    assert len(host.closed) == 29 and all(f.engine is None and f.closed for f in host.fixtures.values())
    for index, case in enumerate(diagnostics["cases"]):
        wire = case["wire_requests"]
        assert [r["response_status"] for r in wire] == [r["status"] for r in case["network"]]
        assert all(r["path"] == "/i/v1/analytics/events" for r in wire)
        configured = next(
            (a["params"]["responses"] for a in actions(ORIGINS[index]) if a["action"] == "configure_mock_responses"), []
        )
        for request, response in zip(wire, configured):
            assert request["response_status"] == response.get("status_code", 200)
            if response.get("body"):
                assert request["response_body"] == response["body"]
            if response.get("headers"):
                assert response["headers"].items() <= request["response_headers"].items()
        assert all(isinstance(r["timestamp_ms"], int) for r in wire)
    assert len(diagnostics["cases"][-1]["wire_requests"]) == 4
    assert [r["response_status"] for r in diagnostics["cases"][17]["wire_requests"]] == [503, 503, 200]
    assert (
        diagnostics["cases"][20]["wire_requests"][1]["timestamp_ms"]
        - diagnostics["cases"][20]["wire_requests"][0]["timestamp_ms"]
        >= 2500
    )


@pytest.mark.parametrize(
    "defect,index,code",
    [
        ("retry_uuid", 0, "retry_event_uuid"),
        ("retry_timestamp", 1, "retry_event_timestamp"),
        ("duplicate_uuids", 3, "batch_duplicate_uuid"),
        ("frozen_attempt", 5, "retry_attempt"),
        ("replace_request_id", 6, "retry_request_id"),
        ("reuse_request_id", 7, "different_request_header"),
        ("frozen_request_timestamp", 8, "different_request_header"),
        ("missing_header:PostHog-Request-Id", 14, "mock_request_id_echo"),
        ("no_retry", 15, "request_count"),
        ("ignore_retry_after", 20, "retry_delay"),
        ("retry_terminal", 21, "terminal_request_count"),
        ("no_backoff", 27, "retry_delay"),
        ("exceed_retry_budget", 28, "request_count"),
    ],
)
async def test_native_http_defects_fail_with_source_and_call_attribution(contracts, tmp_path, defect, index, code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) == len(expected_calls(ORIGINS[index]))
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("runtime", ["server", "browser"])
async def test_non_timing_case_and_all_retry_candidates_ignore_role_identity_products(contracts, runtime):
    async with serve(contracts, host_type=AnalyticsWireHost, runtime=runtime) as (host, url):
        host.profile["products"] = ["flags"]
        for case in CASES:
            assert selection(case, host.profile, host.routes)["selected"]
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[9]])
    assert strict_exit_code(contracts, report) == 0


@pytest.mark.parametrize(
    "options,status,code",
    [
        ({"sdk_capabilities": None}, "not_selected", None),
        ({"missing_route": "/capture"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/setup"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/flush"}, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, "blocked_fixture", "fixture_unavailable"),
    ],
)
async def test_retry_preflight_keeps_independent_operation_and_fixture_gaps(contracts, options, status, code):
    async with serve(contracts, host_type=AnalyticsWireHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert [r["result"]["status"] for r in report["results"]] == [status] * 29
    if code:
        assert all(r["result"]["failure"]["code"] == code for r in report["results"])
    assert not host.fixtures and strict_exit_code(contracts, report) == 1


def observation(events=None, **kwargs):
    return SimpleNamespace(
        parsed_events=events,
        path="/i/v1/analytics/events",
        headers={},
        response_headers={},
        response_body=None,
        response_status=503,
        timestamp_ms=0,
        **kwargs,
    )


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


@pytest.mark.parametrize("field", ["uuid", "timestamp"])
async def test_preservation_uses_only_first_two_filtered_lists_including_null(field):
    text = f'the present event "{field}" lists in requests zero and one should be identical'
    observed = [observation([{}, {field: None}]), observation([{field: None}, {}]), observation([{field: "different"}])]
    await check(text, observed)
    observed[1].parsed_events = [{}]
    with pytest.raises(BoundaryError, match="differ"):
        await check(text, observed)
    observed[0].parsed_events = []
    await check(text, observed)
    with pytest.raises(BoundaryError, match="at least two"):
        await check(text, observed[:1])


async def test_duplicate_check_skips_missing_falsey_values_but_inspects_later_requests():
    observed = [observation([{}, {"uuid": ""}, {"uuid": None}, {"uuid": "same"}]), observation([{"uuid": "same"}])]
    text = "every received batch should have no duplicate nonempty UUIDs"
    await check(text, observed)
    observed[1].parsed_events *= 2
    with pytest.raises(BoundaryError, match="Duplicate"):
        await check(text, observed)


async def test_attempts_and_preserved_request_id_inspect_all_requests_but_difference_only_first_two():
    observed = [observation() for _ in range(3)]
    for i, request in enumerate(observed):
        request.headers = {"posthog-attempt": f"+0{i + 1}", "posthog-request-id": "not-a-uuid"}
    for text in (
        "all recorded request attempts should be consecutive integers starting at one",
        "all recorded request IDs should equal the nonempty first request ID",
    ):
        await check(text, observed)
    observed[-1].headers = {"posthog-attempt": "1", "posthog-request-id": "different"}
    for text in (
        "all recorded request attempts should be consecutive integers starting at one",
        "all recorded request IDs should equal the nonempty first request ID",
    ):
        with pytest.raises(BoundaryError):
            await check(text, observed)
    observed[0].headers["posthog-request-timestamp"] = "not-a-date"
    observed[1].headers["posthog-request-timestamp"] = "different-not-a-date"
    await check('the first two request headers "posthog-request-timestamp" should be nonempty and different', observed)


async def test_any_200_is_not_last_success_and_terminal_count_excludes_flags_substrings():
    observed = [observation(), observation()]
    observed[0].response_status = 200
    await check("at least one recorded response should have status 200", observed)
    observed[0].path = "/anything/flags/other"
    await check("exactly one recorded request excluding paths containing /flags should have been received", observed)
    with pytest.raises(BoundaryError):
        await check("exactly 1 capture request should have been received", observed)


@pytest.mark.parametrize("minimum", [100, 2500])
async def test_delay_checks_first_pair_only_at_exact_floor_without_exponential_assertion(minimum):
    observed = [observation() for _ in range(3)]
    observed[1].timestamp_ms = minimum
    observed[2].timestamp_ms = minimum + 1
    text = f"the first inter-request delay should be at least {minimum} milliseconds"
    await check(text, observed)
    observed[1].timestamp_ms -= 1
    with pytest.raises(BoundaryError, match="below"):
        await check(text, observed)


async def test_mock_results_assertions_do_not_prove_uuid_keys_or_sdk_interpretation():
    first = observation()
    first.response_body = '{"results":{"not-a-uuid":"ok","other":{"result":"ok"},"third":"ok"}}'
    first.response_status = 200
    for text in (
        "the first mock-authored response should contain a results object",
        'every first mock-authored response result should equal "ok"',
        "the first mock-authored response should contain exactly 3 results",
        "the first mock-authored response should have status 200",
    ):
        await check(text, [first, observation()])
    first.response_body = '{"results":{}}'
    await check('every first mock-authored response result should equal "ok"', [first])
    for body, text, code in [
        ('{"results":[]}', "the first mock-authored response should contain a results object", "mock_results_map"),
        (
            '{"results":{"x":"drop"}}',
            'every first mock-authored response result should equal "ok"',
            "mock_result_value",
        ),
        ('{"results":{}}', "the first mock-authored response should contain exactly 3 results", "mock_results_count"),
    ]:
        first.response_body = body
        with pytest.raises(BoundaryError) as error:
            await check(text, [first])
        assert error.value.code == code


async def test_mock_header_checks_preserve_selected_first_response_and_casing():
    first = observation()
    first.response_headers = {"retry-after": "3", "posthog-request-id": "arbitrary-id"}
    first.headers = {"posthog-request-id": "arbitrary-id"}
    await check("the first mock-authored response Retry-After should be present", [first, observation()])
    await check("the first mock-authored response should echo the nonempty sent request ID", [first])
    for text in ("the first mock-authored response Retry-After should be absent",):
        with pytest.raises(BoundaryError):
            await check(text, [first])
    first.response_headers = {"Retry-After": ""}
    await check("the first mock-authored response Retry-After should be absent", [first])
    with pytest.raises(BoundaryError):
        await check("the first mock-authored response should echo the nonempty sent request ID", [first])
    with pytest.raises(BoundaryError):
        await check("the first mock-authored response Retry-After should be present", [first])


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("duplicate_uuids", 1)])
async def test_retry_cli_outside_checkout_failure_and_next_case_isolation(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        code, report, diagnostics, output = await cli_run(
            tmp_path,
            url,
            "--feature",
            FEATURE,
            "--profile",
            host.profile["id"],
            "--case-id",
            IDS[3],
            "--case-id",
            IDS[9],
        )
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert report["results"][9]["result"]["status"] == "passed"
    assert report["results"][3]["result"]["status"] == ("failed_assertion" if defect else "passed")
    assert len(host.closed) == 2
