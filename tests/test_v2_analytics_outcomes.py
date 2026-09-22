"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from posthog_test_harness.types import MockResponse
from posthog_test_harness.v2.analytics_outcome_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import observation, save_receipt
from tests.v2_analytics_wire_host import AnalyticsWireEngine, AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = "migration/yaml-parity-v1/capture-analytics-v1-outcomes.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


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
async def test_distinct_outcome_families_reject_real_http_defects(
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


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)
