"""AI capture contracts respect the adapter's normal capture protocol."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import AssertRequestPathAction
from posthog_test_harness.contract import ContractExecutor
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
