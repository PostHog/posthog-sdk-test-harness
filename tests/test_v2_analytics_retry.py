"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.analytics_retry_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = "migration/yaml-parity-v1/capture-analytics-v1-retry.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


def save_receipt(path, report, diagnostics):
    (path / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (path / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")


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
async def test_native_http_defects_fail_with_source_and_call_attribution(
    contracts, tmp_path, defect, index, code, specs, case_ids
):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        report, diagnostics = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[index]]
        )
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


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


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)
