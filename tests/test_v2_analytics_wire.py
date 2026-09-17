"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.analytics_wire_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import SPECS
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = "migration/yaml-parity-v1/capture-analytics-v1.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


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
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], timeout_ms=60000)
    assert [r["result"]["status"] for r in report["results"]] == ["failed_assertion"] + ["passed"] * 17
    assert len(host.closed) == 18 and strict_exit_code(contracts, report) == 1


async def test_complete_feature_through_public_http(contracts):
    async with serve(contracts, host_type=AnalyticsWireHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], timeout_ms=60000)
    assert strict_exit_code(contracts, report) == 0, report
    assert all(row["result"]["status"] in ("passed", "not_selected") for row in report["results"])
    assert len(host.closed) == sum(row["result"]["executed"] for row in report["results"])
    assert len({d["mock_url"] for d in diagnostics["cases"]}) == len(diagnostics["cases"])
