"""Analytics-v1 source cases 19–40: properties, batching and collected identities."""

import asyncio
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.analytics_wire_steps import STEPS, capture_sequence
from posthog_test_harness.v2.contracts import Contracts, json_equal
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_wire import LEGACY, SOURCE, assertion_texts
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = SUITE + "/capture-analytics-v1-batching.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]
ORIGINS = [row for row in LEGACY if row["source"]["path"] == SOURCE][18:40]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def actions(origin):
    return [row["input"] for row in origin["steps"]]


def expected_assertion(action):
    name, params = action["action"], action.get("params", {})
    if name == "assert_event_property":
        return [
            f'the first received event property "{params["property"]}" should equal JSON '
            + json.dumps(params["expected"])
        ]
    if name == "assert_event_property_is_object":
        return [f'the first received event property "{params["property"]}" should be an object']
    if name == "assert_event_has_field":
        return [f'the first received event should contain root field "{params["field"]}"']
    if name == "assert_events_in_batch_count":
        assert set(params) == {"expected"}
        return [f'the first request should contain exactly {params["expected"]} parsed events']
    if name == "assert_request_count_gte":
        return [f'at least {params["expected"]} capture request should have been received']
    if name == "assert_v1_created_at_recent":
        return [
            f'the first request created_at should be within {params["max_age_seconds"]} seconds '
            "of the current wall clock"
        ]
    if name == "assert_all_uuids_unique":
        return ["all present UUIDs across received requests should be unique"]
    if name == "assert_different_uuids":
        return ["the first two present UUIDs across received requests should differ"]
    if name == "assert_authorization_bearer_token":
        assert params == {}
        return ['the first request should authenticate with bearer token "phc_test_key"']
    return assertion_texts(action)


def test_exact_22_source_inputs_and_ordered_assertions_without_retry_scope():
    assert len(CASES) == len(ORIGINS) == 22
    assert ORIGINS[0]["name"] == "custom_properties_preserved"
    assert ORIGINS[-1]["name"] == "different_events_same_content_different_uuids"
    for case, origin in zip(CASES, ORIGINS):
        row = case.migration
        assert row["legacy_id"] == origin["id"]
        assert row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert row["candidate_routes"] == ["/capture"]
        assert row["sdk_capabilities"] == ["capture_v1"]
        assert row["native_sdk_evidence"] == []
        assert case.id == "migration:yaml-parity-v1:capture_analytics_v1:" + origin["name"]
        assert {"@both", "@api_capture_v1"} <= set(case.tags)
        assert len(case.steps) == len(actions(origin)) + 2
        for step, action in zip(case.steps[2:], actions(origin)):
            name, params = action["action"], action.get("params", {})
            if name == "init":
                assert set(params) <= {"flush_at"}
                suffix = f'flush threshold {params["flush_at"]}' if params else "no additional configuration"
                assert step.text == 'the SDK is initialized with token "phc_test_key" and ' + suffix
            elif name == "capture":
                assert step.text == "capture is called with JSON arguments:"
                assert json_equal(json_arguments(step), params)
            elif name == "capture_multiple":
                assert (
                    step.text == f'capture is called sequentially {params["count"]} times '
                    "with zero-based top-level index substitution:"
                )
                assert json_equal(json_arguments(step), params["params"])
            elif name == "flush":
                assert step.text == "pending captures are flushed"
            elif name == "wait":
                assert step.text == f'{params["duration_ms"]} milliseconds elapse without a public SDK call'
            else:
                actual = [step.text]
                if "dataTable" in step.argument:
                    actual.append([r["cells"][0]["value"] for r in step.argument["dataTable"]["rows"][1:]])
                assert actual == expected_assertion(action)
    discovery = discover(SPECS, [FEATURE])
    assert all(row["status"] == "harness_ready" for row in discovery["cases"])
    assert discovery["cases"][17]["required_routes"] == ["/capture", "/flush", "/setup"]
    assert discovery["cases"][18]["required_routes"] == ["/capture", "/setup"]


def expected_calls(origin):
    calls = []
    for action in actions(origin):
        name, params = action["action"], action.get("params", {})
        if name == "init":
            calls.append(("/setup", {"project_token": "phc_test_key", "config": params}))
        elif name == "capture":
            calls.append(("/capture", params))
        elif name == "capture_multiple":
            for index in range(params["count"]):
                args = {k: v.format(index=index) if isinstance(v, str) else v for k, v in params["params"].items()}
                calls.append(("/capture", args))
        elif name == "flush":
            calls.append(("/flush", {}))
    return calls


async def test_all_22_execute_real_http_with_exact_public_calls_and_omission(contracts):
    async with serve(contracts, host_type=AnalyticsWireHost) as (host, url):
        started = time.monotonic()
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
        assert time.monotonic() - started >= 1
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 22
    actual = []
    for row in host.inputs:
        call = deepcopy(row["invoke"])
        if call["route"] == "/setup":
            assert call["args"]["config"].pop("host").startswith("http://127.0.0.1:")
        actual.append([call["route"], call["args"]])
    assert json_equal(actual, [list(call) for origin in ORIGINS for call in expected_calls(origin)])
    assert len(report["calls"]) == 98
    assert len(host.closed) == 22 and all(f.engine is None and f.closed for f in host.fixtures.values())
    for index, case in enumerate(diagnostics["cases"]):
        assert len(case["network"]) == (0 if index == 17 else 1)
        if index != 17:
            assert case["ingestion"][0]["path"] == "/i/v1/analytics/events"
    assert diagnostics["cases"][15]["ingestion"][0]["event_names"] == [f"test_event_{i}" for i in range(5)]
    assert [c["route"] for c in diagnostics["cases"][17]["invocations"]] == ["/setup", "/flush"]
    assert [c["route"] for c in diagnostics["cases"][18]["invocations"]] == ["/setup"] + ["/capture"] * 3


@pytest.mark.parametrize("runtime", ["browser", "mobile", "edge"])
async def test_batch_and_empty_flush_selection_is_independent_of_runtime_identity_products(contracts, runtime):
    async with serve(contracts, host_type=AnalyticsWireHost, runtime=runtime) as (host, url):
        host.profile["products"] = ["flags"]
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[10], IDS[17]])
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]].count("passed") == 2


@pytest.mark.parametrize(
    "options,explicit,status,code",
    [
        ({"missing_route": "/capture"}, False, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/capture"}, True, "unsupported_binding", "missing_operation"),
        ({"sdk_capabilities": None}, False, "not_selected", None),
        ({"sdk_capabilities": []}, True, "unsupported_binding", "sdk_capability_unavailable"),
        ({"missing_route": "/flush"}, True, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, False, "blocked_fixture", "fixture_unavailable"),
    ],
)
async def test_empty_flush_retains_family_candidate_and_explicit_prerequisite_gaps(
    contracts, options, explicit, status, code
):
    async with serve(contracts, host_type=AnalyticsWireHost, **options) as (host, url):
        report, _ = await run(
            contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[17]] if explicit else []
        )
    result = report["results"][17]["result"]
    assert result["status"] == status, result
    if code:
        assert result["failure"]["code"] == code and not result["executed"]
        if options.get("missing_route") == "/capture":
            assert "/capture" in result["failure"]["message"]
    assert not host.fixtures
    assert strict_exit_code(contracts, report) == 1


DEFECTS = [
    ('property_value:0:custom_string:"wrong"', 0, "event_property"),
    ('property_value:0:custom_number:"42"', 0, "event_property"),
    ("property_value:0:custom_bool:false", 0, "event_property"),
    ("missing_property:custom_bool", 0, "event_property"),
    ("property_value:0:$set:[]", 1, "event_property_object"),
    ("property_value:0:$set_once:null", 2, "event_property_object"),
    ("missing_property:$groups", 3, "event_property_object"),
    *[("array_properties", index, "event_property_object") for index in (1, 2, 3, 11, 12, 13)],
    ("null_properties", 1, "event_property_object"),
    ("missing_uuid", 4, "event_field_missing"),
    ("invalid_uuid", 4, "event_uuid"),
    ("omit_index_field:1:event", 5, "event_fields"),
    ("invalid_uuid", 6, "event_uuid"),
    ("second_event_offset", 7, "event_timestamp"),
    ("numeric_identity", 8, "event_string"),
    ("property_identity", 9, "event_placement"),
    ("property_value:0:custom_number:41", 10, "event_property"),
    ("duplicate_uuids", 14, "uuid_unique"),
    ("short_batch", 15, "batch_count"),
    ("duplicate_request", 15, "request_count"),
    ("missing_header:Authorization", 16, "missing_header"),
    ("omit_body:created_at", 16, "body_format"),
    ("empty_network", 17, "request_count"),
    ("startup_flags", 17, "request_count"),
    ("startup_capture", 17, "request_count"),
    ("suppress_threshold", 18, "request_count"),
    ("created_at_delta:-10", 19, "created_at_recent"),
    ("created_at_delta:10", 19, "created_at_recent"),
    ("offset_created_at", 19, "created_at_recent"),
    ("omit_body:created_at", 19, "created_at_recent"),
    ("non_json_body", 19, "created_at_recent"),
    ("duplicate_uuids", 20, "uuid_unique"),
    ("duplicate_request", 20, "uuid_unique"),
    ("duplicate_uuids", 21, "uuid_pair"),
    ("missing_uuid", 21, "uuid_pair"),
]


@pytest.mark.parametrize("defect,index,code", DEFECTS)
async def test_assertion_families_reject_attributed_http_defects(contracts, defect, index, code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) == len(expected_calls(ORIGINS[index]))
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "variation,index",
    [
        ("property_value:0:custom_bool:1", 0),
        ("property_value:0:custom_number:42.0", 0),
        ("property_value:0:$set:{}", 1),
        ('property_value:0:$set_once:{"other":false}', 2),
        ("property_value:0:$groups:{}", 3),
        ("second_event_invalid_uuid", 6),
        ("second_event_numeric_identity", 8),
        ("null_properties", 9),
        ('property_value:1:custom_number:"wrong"', 10),
        ("property_value:1:$set:null", 11),
        ("property_value:1:$set_once:[]", 12),
        ("property_value:1:$groups:false", 13),
        ("omit_index_field:1:uuid", 14),
        ("duplicate_request", 18),
        ("created_at_delta:-2", 19),
        ("created_at_delta:2", 19),
        ("missing_uuid", 20),
        ("no_delivery", 20),
        ("append_duplicate_uuid", 21),
    ],
)
async def test_weaker_source_assertions_remain_weak(contracts, variation, index):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=variation) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    assert report["results"][index]["result"]["status"] == "passed", report
    assert strict_exit_code(contracts, report) == 0


async def test_sequence_preserves_nested_types_and_only_formats_top_level_strings_sequentially():
    step = deepcopy(CASES[5].steps[3])
    template = {
        "distinct_id": "user_{index}",
        "event": "event_{index}",
        "properties": {
            "nested": {"literal": "{index}"},
            "array": ["{index}", False, 42, None],
            "bool": True,
            "number": 0,
        },
    }
    step.argument["docString"]["content"] = json.dumps(template)
    calls, active = [], False

    async def call(route, args):
        nonlocal active
        assert not active
        active = True
        await asyncio.sleep(0)
        calls.append((route, deepcopy(args)))
        active = False

    await capture_sequence(SimpleNamespace(call=call), step, "3")
    assert calls == [("/capture", {**template, "distinct_id": f"user_{i}", "event": f"event_{i}"}) for i in range(3)]
    assert json_arguments(step) == template


async def test_batch_count_and_uniqueness_use_their_original_request_scopes():
    observed = [SimpleNamespace(parsed_events=[{}] * 5), SimpleNamespace(parsed_events=[{"uuid": "later"}])]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    for step in (CASES[15].steps[-1], CASES[20].steps[-1]):
        handler, args = STEPS.bind(step)
        await handler(ctx, step, *args)
    # UUID pair collection skips absent fields and compares only the first two present values.
    observed[0].parsed_events = [{}, {"uuid": None}, {"uuid": "later"}, {"uuid": "later"}]
    step = CASES[21].steps[-1]
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ('property_value:0:custom_number:"42"', 1)])
async def test_batching_cli_outside_checkout_and_case_isolation(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        code, report, _, output = await cli_run(tmp_path, url, "--feature", FEATURE, "--profile", host.profile["id"])
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert [r["result"]["status"] for r in report["results"]] == ["failed_assertion" if defect else "passed"] + [
        "passed"
    ] * 21
    assert len(host.closed) == 22


async def test_threshold_observation_waits_the_full_real_second_without_calling_sdk():
    step = CASES[18].steps[-2]
    handler, args = STEPS.bind(step)
    started = time.monotonic()
    await handler(SimpleNamespace(), step, *args)
    assert time.monotonic() - started >= 1
