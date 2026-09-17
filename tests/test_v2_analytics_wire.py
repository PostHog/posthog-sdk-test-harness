"""First 18 analytics-v1 YAML cases: exact inputs, selection and received-wire checks."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.analytics_wire_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, migration_manifest, migration_paths
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = SUITE + "/capture-analytics-v1.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [c.id for c in CASES]
SOURCE = "contracts/capture_analytics_v1_tests.yaml"
LEGACY = [json.loads(line) for line in (SPECS / "coverage/harness-v2/legacy-cases.jsonl").read_text().splitlines()]
ORIGINS = [row for row in LEGACY if row["source"]["path"] == SOURCE][:18]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def assertion_texts(action):
    """Test-only assertion cross-reference; production never interprets YAML actions."""
    name, params = action["action"], action.get("params", {})
    if name == "assert_request_count":
        return [f'exactly {params["expected"]} capture request should have been received']
    if name == "assert_request_path":
        return ["every capture request path should be one of:", [params["expected"]]]
    if name == "assert_no_requests_to_paths":
        return [f'no capture request should use "{path}"' for path in params["paths"]]
    if name == "assert_authorization_bearer_token":
        return [f'the first request should authenticate with bearer token "{params["expected"]}"']
    if name == "assert_header_value_matches":
        return [f'the first request header "{params["header"]}" should match "{params["pattern"]}"']
    if name == "assert_header_is_integer":
        return [f'the first request header "{params["header"]}" should be integer {params["expected"]}']
    if name in ("assert_header_is_uuid", "assert_header_is_rfc3339"):
        expectation = "a valid UUID" if name.endswith("uuid") else "a canonical UTC timestamp"
        return [f'the first request header "{params["header"]}" should be {expectation}']
    if name == "assert_v1_body_format":
        return ["the first request body should have a canonical UTC created_at and a nonempty batch array"]
    if name == "assert_body_field_absent":
        return ["the first request body should omit these root fields:", params["fields"]]
    if name == "assert_v1_event_format":
        return [
            "every event in the first capture request should contain these root fields:",
            ["event", "uuid", "distinct_id", "timestamp"],
        ]
    if name == "assert_uuid_format":
        assert params == {"field": "uuid"}
        return ["the first received event UUID should be valid"]
    if name == "assert_event_field_is_rfc3339":
        assert params["field"] == "timestamp"
        if "expected" in params:
            return [f'every event in the first capture request should have UTC timestamp "{params["expected"]}"']
        return ["every event in the first capture request should have a canonical UTC timestamp"]
    if name == "assert_event_property":
        return [f'the first received event property "{params["property"]}" should equal "{params["expected"]}"']
    if name == "assert_event_field_is_string":
        return [f'the first received event field "{params["field"]}" should be a string']
    assert name == "assert_event_field_not_in_properties"
    return [f'the first received event should contain "{params["field"]}" at root and not in properties']


def test_all_18_have_exact_source_inputs_filters_and_ordered_assertion_traceability():
    assert len(CASES) == len(ORIGINS) == 18
    assert ORIGINS[0]["name"] == "targets_v1_endpoint"
    assert ORIGINS[-1]["name"] == "distinct_id_at_root_not_properties"
    for case, origin in zip(CASES, ORIGINS):
        row = case.migration
        assert row["legacy_id"] == origin["id"]
        assert row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert row["candidate_routes"] == ["/capture"]
        assert row["sdk_capabilities"] == ["capture_v1"]
        assert row["native_sdk_evidence"] == []
        assert case.id == "migration:yaml-parity-v1:capture_analytics_v1:" + origin["name"]
        assert "@both" in case.tags and "@api_capture_v1" in case.tags
        actions = [s["input"] for s in origin["steps"]]
        assert actions[0]["params"] in ({"flush_at": 1}, {"api_key": "phc_test_key", "flush_at": 1})
        assert case.steps[2].text == 'the SDK is initialized with token "phc_test_key" and flush threshold 1'
        assert json_arguments(case.steps[3]) == actions[1]["params"]
        assert actions[1]["action"] == "capture" and actions[2]["action"] == "flush"
        assert case.steps[4].text == "pending captures are flushed"
        expected = [text for a in actions[3:] for text in assertion_texts(a)]
        actual = []
        for step in case.steps[5:]:
            actual.append(step.text)
            if "dataTable" in step.argument:
                actual.append([r["cells"][0]["value"] for r in step.argument["dataTable"]["rows"][1:]])
        assert actual == expected
    manifest = migration_manifest(SPECS)
    source = next(s for s in manifest["legacy_sources"] if s["path"] == SOURCE)
    assert source["sha256"] == hashlib.sha256((SPECS.parent / "harness" / SOURCE).read_bytes()).hexdigest()
    migrated = discover(SPECS, migration_paths(SPECS))
    assert len(migrated["cases"]) == 157
    assert all(c["status"] == "harness_ready" for c in migrated["cases"])
    canonical = discover(SPECS)
    assert len(canonical["cases"]) == 728
    assert sum(c["status"] == "harness_ready" for c in canonical["cases"]) == 57
    assert not set(IDS) & {c["case_id"] for c in canonical["cases"]}


@pytest.mark.parametrize("runtime", ["server", "browser", "mobile", "edge"])
async def test_18_cases_execute_through_public_operations_on_all_runtime_families(contracts, runtime):
    async with serve(contracts, host_type=AnalyticsWireHost, runtime=runtime) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 18
    assert len(host.closed) == 18 and len(report["calls"]) == 54
    assert all(f.engine is None and f.closed for f in host.fixtures.values())
    calls = [row["invoke"] for row in host.inputs]
    assert [c["route"] for c in calls] == ["/setup", "/capture", "/flush"] * 18
    for i, case in enumerate(CASES):
        setup, capture, flush = calls[i * 3 : i * 3 + 3]
        config = setup["args"]["config"]
        assert setup["args"] == {"project_token": "phc_test_key", "config": {"host": config["host"], "flush_at": 1}}
        assert capture["args"] == json_arguments(case.steps[3])
        assert flush["args"] == {}
    assert all(
        len(d["network"]) == 1 and d["ingestion"][0]["path"] == "/i/v1/analytics/events" for d in diagnostics["cases"]
    )


@pytest.mark.parametrize(
    "options,status,code",
    [
        ({"sdk_capabilities": None}, "not_selected", None),
        ({"sdk_capabilities": []}, "not_selected", None),
        ({"sdk_capabilities": ["capture_ai_v0"]}, "not_selected", None),
        ({"sdk_capabilities": [], "missing_route": "/capture"}, "not_selected", None),
        ({"missing_route": "/capture"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/setup"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/flush"}, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, "blocked_fixture", "fixture_unavailable"),
    ],
)
async def test_selection_uses_capture_and_concrete_v1_claim_not_runtime_or_support_routes(
    contracts, options, status, code
):
    async with serve(contracts, host_type=AnalyticsWireHost, **options) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert [r["result"]["status"] for r in report["results"]] == [status] * 18
    assert strict_exit_code(contracts, report) == 1
    assert not host.fixtures
    assert len(diagnostics["selection"]) == 18
    if code:
        assert all(r["result"]["failure"]["code"] == code for r in report["results"])


@pytest.mark.parametrize(
    "options,code",
    [
        ({"sdk_capabilities": None}, "sdk_capability_unavailable"),
        ({"sdk_capabilities": [], "missing_route": "/capture"}, "missing_operation"),
        ({"missing_route": "/setup"}, "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, "fixture_unavailable"),
    ],
)
async def test_explicit_case_selection_keeps_missing_prerequisites(contracts, options, code):
    async with serve(contracts, host_type=AnalyticsWireHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    assert report["results"][0]["result"]["failure"]["code"] == code
    assert not host.fixtures and strict_exit_code(contracts, report) == 1
    assert [r["result"]["status"] for r in report["results"][1:]] == ["not_selected"] * 17


async def test_v1_claim_does_not_reconfigure_legacy_wire_profile(contracts):
    async with serve(contracts, host_type=AnalyticsWireHost, protocol="legacy", runtime="browser") as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    assert report["results"][0]["result"]["failure"]["code"] == "request_path"
    assert diagnostics["cases"][0]["ingestion"][0]["path"] == "/batch"
    assert report["profiles"][0]["protocol"] == "legacy"
    assert strict_exit_code(contracts, report) == 1


DEFECTS = [
    ("wrong_route", 0, "request_path"),
    ("duplicate_request", 0, "request_count"),
    ("startup_capture", 0, "request_count"),
    ("startup_flags", 0, "request_count"),
    ("no_delivery", 0, "request_count"),
    *[("path:" + path, 1, "request_path") for path in ("/batch/", "/e/", "/capture/", "/track/", "/i/v0/e/")],
    ("missing_header:Authorization", 2, "missing_header"),
    ("header:Authorization:Basic phc_test_key", 2, "bearer_token"),
    ("header:Authorization:Bearer wrong", 2, "bearer_token"),
    ("header:Authorization:Bearer\tphc_test_key", 2, "bearer_token"),
    ("header:Content-Type:text/plain", 3, "header_pattern"),
    ("header:PostHog-Sdk-Info:/1", 4, "header_pattern"),
    ("header:PostHog-Attempt:1.0", 5, "header_integer"),
    ("header:PostHog-Attempt:2", 5, "header_integer"),
    ("header:PostHog-Request-Id:invalid", 6, "header_uuid"),
    ("header:PostHog-Request-Timestamp:2025-02-30T03:04:05Z", 7, "header_timestamp"),
    ("header:PostHog-Request-Timestamp:2025-01-02T08:34:05+05:30", 7, "header_timestamp"),
    ("header:User-Agent:", 8, "header_pattern"),
    *[
        ("missing_header:" + header, index, "missing_header")
        for index, header in enumerate(
            [
                "Content-Type",
                "PostHog-Sdk-Info",
                "PostHog-Attempt",
                "PostHog-Request-Id",
                "PostHog-Request-Timestamp",
                "User-Agent",
            ],
            3,
        )
    ],
    *[
        (defect, 9, "body_format")
        for defect in (
            "omit_body:created_at",
            "offset_created_at",
            "omit_body:batch",
            "empty_batch",
            "object_batch",
            "non_json_body",
            "empty_body",
        )
    ],
    ("root:api_key", 10, "body_field_present"),
    ("root:token", 10, "body_field_present"),
    ("root:sent_at", 11, "body_field_present"),
    *[("omit_event:" + field, 12, "event_fields") for field in ("event", "uuid", "timestamp", "distinct_id")],
    ("second_event_missing_root", 12, "event_fields"),
    ("missing_uuid", 13, "event_uuid"),
    ("invalid_uuid", 13, "event_uuid"),
    ("omit_event:timestamp", 14, "event_timestamp"),
    ("timestamp:2025-01-02t03:04:05z", 14, "event_timestamp"),
    ("timestamp:2025-02-30T03:04:05Z", 14, "event_timestamp"),
    ("second_event_offset", 14, "event_timestamp"),
    ("offset_timestamp", 15, "event_timestamp"),
    ("wrong_instant", 15, "event_timestamp"),
    ("nanosecond_drift", 15, "event_timestamp"),
    ("second_event_timestamp", 15, "event_timestamp"),
    ("rewrite_property", 15, "event_property"),
    ("numeric_identity", 16, "event_string"),
    ("omit_event:distinct_id", 16, "event_string"),
    ("property_identity", 17, "event_placement"),
    ("omit_event:distinct_id", 17, "event_placement"),
]


@pytest.mark.parametrize("defect,index,code", DEFECTS)
async def test_each_wire_assertion_family_rejects_real_http_defects(contracts, defect, index, code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) == 3
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "variation,index",
    [
        ("trailing_slash", 0),
        ("header:Authorization:bEaReR   phc_test_key  ", 2),
        ("header:Content-Type:application/json;charset=utf-8", 3),
        ("header:Content-Type:application/json-extra", 3),
        ("header:PostHog-Sdk-Info:one/two/three", 4),
        ("header:PostHog-Attempt:+01", 5),
        ("header:PostHog-Request-Id:00000000000000000000000000000000", 6),
        ("header:PostHog-Request-Timestamp:2025-01-02T03:04:05.123456789+00:00", 7),
        ("second_request_bad_header", 2),
        ("empty_body", 10),
        ("non_json_body", 10),
        ("nested_token", 10),
        ("empty_body", 11),
        ("non_json_body", 11),
        ("null_required_fields", 12),
        ("second_event_invalid_uuid", 13),
        ("timestamp:2025-01-02T03:04:05.123456789Z", 14),
        ("timestamp:2025-01-02T03:04:05.000000000+00:00", 15),
        ("empty_identity", 16),
        ("second_event_numeric_identity", 16),
        ("null_properties", 17),
        ("null_identity", 17),
    ],
)
async def test_source_assertions_are_not_silently_strengthened(contracts, variation, index):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=variation) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    assert report["results"][index]["result"]["status"] == "passed", report
    assert strict_exit_code(contracts, report) == 0


async def test_every_path_is_observed_but_header_body_and_events_use_first_request_only():
    observed = [SimpleNamespace(path="/i/v1/analytics/events/", method="GET"), SimpleNamespace(path="/batch/")]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = CASES[0].steps[-1]
    handler, args = STEPS.bind(step)
    with pytest.raises(BoundaryError, match="Unexpected capture request path"):
        await handler(ctx, step, *args)
    observed[1].path = "/i/v1/analytics/events"
    await handler(ctx, step, *args)
    observed[0].headers = {"posthog-attempt": "1"}
    observed[0].body_decompressed = '{"created_at":"2025-01-02T03:04:05Z","batch":[{}]}'
    observed[0].parsed_events = [{"timestamp": "2025-01-02T03:04:05Z"}]
    for index in (5, 9, 14):
        step = CASES[index].steps[-1]
        handler, args = STEPS.bind(step)
        await handler(ctx, step, *args)


async def test_defect_does_not_leak_into_later_cases(contracts):
    async with serve(contracts, host_type=AnalyticsWireHost, defect="wrong_route") as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert [r["result"]["status"] for r in report["results"]] == ["failed_assertion"] + ["passed"] * 17
    assert len(host.closed) == 18 and strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("wrong_route", 1)])
async def test_analytics_wire_cli_outside_checkout(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        # Keep this CLI regression scoped to the existing 40 wire/batching cases;
        # the retry module owns the real-time retry scenarios.
        previous_cases, _ = load_cases(SPECS, [FEATURE, SUITE + "/capture-analytics-v1-batching.feature"])
        selectors = [arg for case in previous_cases for arg in ("--case-id", case.id)]
        code, report, _, output = await cli_run(
            tmp_path, url, "--migration-suite", "--profile", host.profile["id"], *selectors
        )
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert len(report["results"]) == 157
    assert [r["result"]["status"] for r in report["results"][:5]] == ["not_selected"] * 5
    assert [r["result"]["status"] for r in report["results"][5:45]] == ["failed_assertion" if defect else "passed"] + [
        "passed"
    ] * 39
    assert [r["result"]["status"] for r in report["results"][45:]] == ["not_selected"] * 112


async def test_product_lane_does_not_override_independent_api_declaration(contracts):
    async with serve(contracts, host_type=AnalyticsWireHost) as (host, url):
        host.profile["products"] = ["flags"]
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    assert strict_exit_code(contracts, report) == 0
    assert report["results"][0]["result"]["status"] == "passed"
