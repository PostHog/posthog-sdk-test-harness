"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.legacy_capture_steps import STEPS
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import observation, save_receipt
from tests.v2_flush_host import serve
from tests.v2_legacy_capture_host import LegacyCaptureHost

FEATURE = "migration/yaml-parity-v1/capture-legacy.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


@pytest.mark.parametrize(
    "defect,number,variant,code",
    [
        ("wrong_root_identity", 1, "batch", "event_field"),
        ("wrong_property_identity", 2, "event", "event_property"),
        ("missing_uuid", 3, "batch", "event_field_missing"),
        ("invalid_uuid", 3, "batch", "event_uuid"),
        ("omit_lib", 4, "batch", "event_property_missing"),
        ("missing_token", 7, "batch", "request_token"),
        ("missing_token", 8, "event", "event_token"),
        ("shadow_event_token", 8, "event", "event_token"),
        ("property_value:0:custom_number:43", 9, "batch", "event_property"),
        ("omit_event:timestamp", 10, "batch", "event_field_missing"),
        ("offset_timestamp", 11, "batch", "event_timestamp"),
        ("rewrite_property", 11, "batch", "event_property"),
        ("no_retry", 18, "batch", "request_count"),
        ("ignore_retry_after", 15, "batch", "retry_delay"),
        ("no_backoff", 16, "batch", "retry_delay"),
        ("no_retry", 20, "batch", "request_count"),
        ("duplicate_uuids", 21, "batch", "uuid_unique"),
        ("retry_uuid", 22, "batch", "retry_event_uuid"),
        ("retry_timestamp", 24, "batch", "retry_event_timestamp"),
        ("duplicate_uuids", 25, "batch", "batch_duplicate_uuid"),
        ("duplicate_uuids", 26, "batch", "uuid_pair"),
        ("omit_body:api_key", 28, "batch", "legacy_batch"),
        ("object_batch", 30, "batch", "legacy_batch"),
        ("startup_capture", 29, "batch", "request_count"),
        ("duplicate_request", 31, "batch", "request_count"),
    ],
)
async def test_distinct_legacy_defects_fail_real_http_with_source_attribution(
    contracts, tmp_path, defect, number, variant, code, specs, feature_cases, case_ids
):
    case = feature_cases[number - 1 if number < 27 else number - 2]
    async with serve(contracts, host_type=LegacyCaptureHost, wire_variant=variant, defect=defect) as (host, url):
        report, diagnostics = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id], timeout_ms=60000
        )
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][case_ids.index(case.id)]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


async def test_token_locations_first_request_all_events_and_first_truthy_precedence():
    for event, body in [({"token": "key"}, "not-json"), ({}, '{"api_key":"key"}'), ({}, '{"token":"key"}')]:
        await check(BATCH_TOKEN, [observation([{}, event], body_decompressed=body), observation()])
    for event, body in [({"api_key": "key"}, "{}"), ({"properties": {"token": "key"}}, "{}"), ({}, "not-json")]:
        with pytest.raises(BoundaryError):
            await check(BATCH_TOKEN, [observation([event], body_decompressed=body), observation([{"token": "key"}])])
    for event in [
        {"token": "key"},
        {"api_key": "key"},
        {"properties": {"token": "key"}},
        {"properties": {"api_key": "key"}},
        {"token": "", "api_key": "key"},
    ]:
        await check(EVENT_TOKEN, [observation([{}, event], body_decompressed="{}")])
    for event in [
        {},
        {"token": "wrong", "properties": {"token": "key"}},
        {"api_key": "wrong", "properties": {"api_key": "key"}},
        {"properties": {"token": "wrong", "api_key": "key"}},
    ]:
        with pytest.raises(BoundaryError):
            await check(
                EVENT_TOKEN,
                [observation([event], body_decompressed='{"api_key":"key"}'), observation([{"token": "key"}])],
            )
    for text in (BATCH_TOKEN, EVENT_TOKEN):
        with pytest.raises(BoundaryError):
            await check(text, [])


async def test_presence_identity_batch_and_uuid_assertions_retain_source_weaknesses():
    first = observation(
        [{"uuid": None, "timestamp": None, "properties": {"$lib": None}}],
        body_decompressed='{"api_key":null,"batch":[]}',
    )
    for text in [
        'the first received event should contain root field "uuid"',
        'the first received event should contain root field "timestamp"',
        'the first received event should contain property "$lib"',
        "the first request body should contain a batch array and an api_key field",
    ]:
        await check(text, [first, observation()])
    first.body_decompressed = '{"batch":[]}'
    await check("the first request body should contain a batch array", [first])
    with pytest.raises(BoundaryError):
        await check("the first request body should contain a batch array and an api_key field", [first])
    first.parsed_events = [{"distinct_id": "test_user", "properties": {"distinct_id": "test_user"}}, {}]
    await check('the first received event field "distinct_id" should equal "test_user"', [first])
    await check('the first received event property "distinct_id" should equal JSON "test_user"', [first])
    for text in [
        "all present UUIDs across received requests should be unique",
        'the present event "uuid" lists in requests zero and one should be identical',
        'the present event "timestamp" lists in requests zero and one should be identical',
        "every received batch should have no duplicate nonempty UUIDs",
    ]:
        await check(text, [first, observation([{}]), observation([{"uuid": "later"}])])
    first.parsed_events = [{"uuid": "0198c0de-0000-4000-8000-000000000abc"}, {"uuid": "invalid"}]
    await check("the first received event UUID should be valid", [first])
    first.parsed_events = [{"properties": {"custom_number": True}}]
    await check('the first received event property "custom_number" should equal JSON 1', [first])


async def test_legacy_counts_include_flags_and_first_delay_is_not_exponential_proof():
    observed = [observation(), observation(), observation()]
    observed[0].path = "/flags"
    observed[0].response_status = 200
    observed[1].timestamp_ms, observed[2].timestamp_ms = 100, 101
    await check("exactly 3 capture request should have been received", observed)
    await check("at least one recorded response should have status 200", observed)
    await check("the first inter-request delay should be at least 100 milliseconds", observed)
    with pytest.raises(BoundaryError):
        await check("exactly 2 capture request should have been received", observed)


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


BATCH_TOKEN = 'the first request should contain token "key" at event token or body api_key or token'

EVENT_TOKEN = 'an event in the first request should resolve token "key" from event then property token or api_key'
