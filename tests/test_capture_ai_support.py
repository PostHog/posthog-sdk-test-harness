"""AI capture contracts respect the adapter's normal capture protocol."""

from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.context import TestContext as HarnessContext
from posthog_test_harness.tests.suites import ContractTestSuite


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability,path,expected_pass",
    [
        ("capture_v0", "/batch", True),
        ("capture_v1", "/i/v1/analytics/events", True),
        ("capture_v0", "/i/v0/ai/batch/", False),
        ("capture_v1", "/i/v0/ai/batch/", False),
        ("capture_v0", "/i/v1/analytics/events", False),
        ("capture_v1", "/batch", False),
    ],
)
async def test_normal_capture_routing_contract(capability: str, path: str, expected_pass: bool) -> None:
    suite = ContractTestSuite("capture_ai", ContractExecutor())
    tests = suite.collect_tests("server", ["capture_ai_v0", capability])
    routing = [(name, test) for name, test in tests if "capture_does_not_reroute_ai_named_events" in name]
    assert len(tests) == 5  # Four shared AI tests plus exactly one protocol-specific routing test.
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
