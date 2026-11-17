"""The four legacy /flags fixtures must work for legacy, rich-v2 and tolerant v4 readers."""

import json
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.tests.context import TestContext as HarnessContext
from posthog_test_harness.tests.suites import ContractTestSuite
from tests.test_feature_flag_rules_v2_wire import check_reader_expectation

CASES = [
    ("request_lifecycle.mock_response_value_is_returned_to_caller", "signup-aa-test", "variant-a", [200]),
    ("retry_behavior.retries_flags_on_502", "transient-502-flag", True, [502, 200]),
    ("retry_behavior.retries_flags_on_504", "transient-504-flag", True, [504, 200]),
    ("side_effect_events.get_feature_flag_captures_feature_flag_called_event", "my-flag", True, [200]),
]


class FlagReaderAdapter:
    def __init__(self, state, reader):
        self.client = MockServer(state).app.test_client()
        self.reader = reader
        self.reset = AsyncMock()
        self.init = AsyncMock()
        self.flush = AsyncMock()

    async def get_feature_flag(self, request):
        for _ in range(2):
            response = self.client.post(f"/flags/?v={'4' if self.reader == 'v4' else '2'}", json={})
            if response.status_code == 200:
                break
        body = response.get_json()
        if self.reader == "legacy":
            value = body["featureFlags"][request.key]
        else:
            record = body["flags"][request.key]
            assert record["key"] == request.key
            assert isinstance(record["enabled"], bool)
            assert isinstance(record["metadata"]["id"], int)
            value = record.get("variant") if record.get("variant") is not None else record["enabled"]
            if self.reader == "v4":
                # This is the existing v4 contract's old-server compatibility branch,
                # not a claim that a rich-v2 record is a v4 producer record.
                assert "value" not in record
                check_reader_expectation(
                    body,
                    {
                        "requested_key": request.key,
                        "value": value,
                        "caller_default": False,
                        "error_code": None,
                        "config_version": 1,
                        "experiment_exposure": False,
                    },
                )
            assert body["featureFlags"][request.key] == value
        self.client.post(
            "/batch/",
            json={
                "batch": [
                    {
                        "event": "$feature_flag_called",
                        "properties": {"$feature_flag": request.key, "$feature_flag_response": value},
                    }
                ]
            },
        )
        return {"success": True, "value": value}


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["legacy", "rich_v2", "v4"])
@pytest.mark.parametrize("name,key,expected,statuses", CASES, ids=[case[0] for case in CASES])
async def test_explicit_flag_fixture_is_compatible_through_mock_http_and_contract_actions(
    reader, name, key, expected, statuses
):
    executor = ContractExecutor()
    suite = ContractTestSuite("feature_flags", executor)
    tests = dict(suite.collect_tests("server", ["capture_v0", *(["flags_response_v4"] if reader == "v4" else [])]))
    state = MockServerState()
    ctx = HarnessContext(FlagReaderAdapter(state, reader), state, "http://mock")
    result = await suite.run_single_test(name, tests[name], ctx)
    assert result.passed, result.message
    assert ctx.last_action_result == {"success": True, "value": expected}
    requests = [r for r in state.get_requests() if r.path.rstrip("/") == "/flags"]
    assert [r.response_status for r in requests] == statuses
    body = json.loads(requests[-1].response_body)
    assert body["featureFlags"][key] == expected
    assert body["flags"][key]["key"] == key
