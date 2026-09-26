"""AI capture contracts respect the adapter's normal capture protocol."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import AssertRequestPathAction
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.endpoints.capture import CaptureEndpoint
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.context import TestContext as HarnessContext
from posthog_test_harness.tests.suites import ContractTestSuite


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [["capture_v0"], ["capture_v1"], ["capture_v0", "capture_v1"]])
@pytest.mark.parametrize(
    "path,expected_pass",
    [
        ("/batch", True),
        ("/i/v1/analytics/events", True),
        ("/i/v0/ai/batch/", False),
        ("/unexpected", False),
    ],
)
async def test_normal_capture_routing_contract(capabilities: list[str], path: str, expected_pass: bool) -> None:
    suite = ContractTestSuite("capture_ai", ContractExecutor())
    tests = suite.collect_tests("server", ["capture_ai_v0", *capabilities])
    routing = [(name, test) for name, test in tests if "capture_does_not_reroute_ai_named_events" in name]
    assert len(tests) == 5  # Four shared AI tests plus one normal-capture routing test.
    assert len(routing) == 1

    state = MockServerState()
    adapter = AsyncMock(spec=SDKAdapterClient)

    async def capture(request):
        assert request.event == "$ai_generation"
        state.record_request("POST", path, {}, {}, b"{}")

    adapter.capture.side_effect = capture
    ctx = HarnessContext(adapter, state, "http://localhost:8081")
    name, test = routing[0]
    result = await suite.run_single_test(name, test, ctx)
    assert result.passed is expected_pass, result.message
    adapter.capture.assert_awaited_once()
    adapter.capture_ai.assert_not_awaited()


@pytest.mark.parametrize("capabilities", [[], ["capture_v0"], ["capture_v1"]])
def test_ai_contract_remains_opt_in(capabilities: list[str]) -> None:
    suite = ContractTestSuite("capture_ai", ContractExecutor())
    assert suite.collect_tests("server", capabilities) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expected,paths,passes",
    [
        ("/batch", ["/batch/"], True),
        ("/batch", ["/i/v1/analytics/events"], False),
        (["/batch", "/i/v1/analytics/events"], ["/batch/", "/i/v1/analytics/events/"], True),
        (["/batch", "/i/v1/analytics/events"], ["/batch", "/i/v0/ai/batch/"], False),
        ([], ["/batch"], False),
        (["/batch"], [], False),
    ],
)
async def test_request_path_assertion(expected, paths: list[str], passes: bool) -> None:
    state = MockServerState()
    for path in paths:
        state.record_request("POST", path, {}, {}, b"{}")
    ctx = SimpleNamespace(mock_server=state)
    if passes:
        await AssertRequestPathAction().execute({"expected": expected}, ctx)
    else:
        with pytest.raises(AssertionError):
            await AssertRequestPathAction().execute({"expected": expected}, ctx)


@pytest.mark.parametrize("capabilities", [[], ["capture_v1"], ["capture_ai_v0"]])
def test_v1_ai_contract_remains_opt_in(capabilities: list[str]) -> None:
    suite = ContractTestSuite("capture_ai_v1", ContractExecutor())
    assert suite.collect_tests("server", capabilities) == []


EVENT_OPTIONS_TESTS = {
    "event_options.unknown_option_passes_through",
    "event_options.option_value_is_not_converted",
    "event_options.option_wins_over_legacy_property",
    "event_options.legacy_property_fills_unset_option",
    "event_options.null_option_falls_back_to_legacy_property",
}


@pytest.mark.parametrize("suite_name", ["capture_v1", "capture_ai_v1"])
def test_event_options_contract_is_opt_in(suite_name: str) -> None:
    suite = ContractTestSuite(suite_name, ContractExecutor())

    def gated(capabilities: list[str]) -> set[str]:
        return {name for name, _ in suite.collect_tests("server", capabilities)} & EVENT_OPTIONS_TESTS

    assert gated([suite_name]) == set()
    assert gated([suite_name, "event_options"]) == EVENT_OPTIONS_TESTS


@pytest.mark.parametrize("path", ["/i/v1/ai/events", "/i/v1/ai/events/"])
def test_mock_answers_v1_ai_path_with_v1_results(path: str) -> None:
    assert (path, "POST") in {(route, method) for route, method, _ in CaptureEndpoint().routes()}

    uuid = "0198c0de-0000-7000-8000-000000000abc"
    body = json.dumps({"created_at": "2025-01-02T03:04:05Z", "batch": [{"uuid": uuid, "event": "$ai_generation"}]})
    request = MockServerState().record_request("POST", path, {"posthog-request-id": "rid-1"}, {}, body.encode())

    assert json.loads(request.response_body) == {"results": {uuid: {"result": "ok"}}}
    assert request.response_headers["PostHog-Request-Id"] == "rid-1"
    assert "Date" in request.response_headers
