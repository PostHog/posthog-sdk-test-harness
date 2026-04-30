import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import (
    AssertActionResultAction,
    AssertEventCountWithNameAction,
    AssertEventPropertyInNamedEventAction,
    AssertFlagsRequestQueryParamAction,
    GetFeatureFlagAction,
)
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import ScopedSDKAdapterClient, SDKAdapterClient
from posthog_test_harness.types import FeatureFlagRequest, MockResponse, RecordedRequest


@pytest.mark.asyncio
async def test_get_feature_flag_action_passes_force_remote() -> None:
    expected_result = {"success": True, "value": True}
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(return_value=expected_result))
    ctx = SimpleNamespace(sdk_adapter=adapter)

    result = await GetFeatureFlagAction().execute(
        {
            "key": "my-flag",
            "distinct_id": "user-123",
            "person_properties": {"email": "user@example.com"},
            "groups": {"company": "acme"},
            "group_properties": {"company": {"plan": "enterprise"}},
            "disable_geoip": True,
            "force_remote": True,
        },
        ctx,
    )

    assert result == expected_result
    adapter.get_feature_flag.assert_awaited_once_with(
        FeatureFlagRequest(
            key="my-flag",
            distinct_id="user-123",
            person_properties={"email": "user@example.com"},
            groups={"company": "acme"},
            group_properties={"company": {"plan": "enterprise"}},
            disable_geoip=True,
            force_remote=True,
        )
    )


class _FakeResponse:
    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict:
        return {"success": True, "value": False}


class _FakeSession:
    def __init__(self, captured: dict) -> None:
        self.captured = captured

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.captured["url"] = url
        self.captured["json"] = json
        return _FakeResponse()


@pytest.mark.asyncio
async def test_sdk_adapter_client_serializes_force_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def make_session() -> _FakeSession:
        return _FakeSession(captured)

    monkeypatch.setattr("posthog_test_harness.sdk_adapter.client.aiohttp.ClientSession", make_session)

    client = SDKAdapterClient("http://adapter")
    result = await client.get_feature_flag(
        FeatureFlagRequest(
            key="my-flag",
            distinct_id="user-123",
            force_remote=True,
        )
    )

    assert result == {"success": True, "value": False}
    assert captured["url"] == "http://adapter/get_feature_flag"
    assert captured["json"] == {
        "key": "my-flag",
        "distinct_id": "user-123",
        "force_remote": True,
    }


@pytest.mark.asyncio
async def test_scoped_sdk_adapter_client_serializes_force_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def make_session() -> _FakeSession:
        return _FakeSession(captured)

    monkeypatch.setattr("posthog_test_harness.sdk_adapter.client.aiohttp.ClientSession", make_session)

    client = ScopedSDKAdapterClient(SDKAdapterClient("http://adapter"), "test-123")
    result = await client.get_feature_flag(
        FeatureFlagRequest(
            key="my-flag",
            distinct_id="user-123",
            force_remote=True,
        )
    )

    assert result == {"success": True, "value": False}
    assert captured["url"] == "http://adapter/get_feature_flag?test_id=test-123"
    assert captured["json"] == {
        "key": "my-flag",
        "distinct_id": "user-123",
        "force_remote": True,
    }


def test_mock_server_flags_endpoint_merges_configured_success_body() -> None:
    state = MockServerState()
    state.set_response_queue(
        [
            MockResponse(
                body=json.dumps(
                    {
                        "featureFlags": {"my-flag": True},
                        "featureFlagPayloads": {"my-flag": "test-payload"},
                    }
                )
            )
        ]
    )
    client = MockServer(state).app.test_client()

    response = client.post("/flags", json={"distinct_id": "user-123"})

    assert response.status_code == 200
    assert response.get_json() == {
        "featureFlags": {"my-flag": True},
        "featureFlagPayloads": {"my-flag": "test-payload"},
        "errorsWhileComputingFlags": False,
    }


def test_feature_flag_contract_asserts_top_level_distinct_id_on_flags_request() -> None:
    """Both posthog-python and @posthog/node send distinct_id at the top level of
    the /flags body. The scaffolding test must lock that down so a regression to
    person_properties-only is caught."""
    contract = ContractExecutor()
    feature_flag_test = contract.get_test_suites()["feature_flags"]["categories"]["request_payload"]["tests"][0]

    get_feature_flag_step = next(step for step in feature_flag_test["steps"] if step["action"] == "get_feature_flag")
    assert get_feature_flag_step["params"]["distinct_id"] == "test_user_123"

    assert any(
        step["action"] == "assert_flags_request_field"
        and step["params"]["field"] == "distinct_id"
        and step["params"]["expected"] == "test_user_123"
        for step in feature_flag_test["steps"]
    )


def _make_recorded(
    path: str,
    query_params: dict | None = None,
    parsed_events: list | None = None,
) -> RecordedRequest:
    return RecordedRequest(
        timestamp_ms=0,
        method="POST",
        path=path,
        headers={},
        query_params=query_params or {},
        body_raw=b"",
        body_decompressed=None,
        parsed_events=parsed_events,
        response_status=200,
        response_headers={},
        response_body=None,
    )


class _FakeMockServer:
    def __init__(self, requests):
        self._requests = requests

    def get_requests(self):
        return list(self._requests)


def _ctx(mock_server, last_action_result=None):
    return SimpleNamespace(mock_server=mock_server, last_action_result=last_action_result)


@pytest.mark.asyncio
async def test_assert_flags_request_query_param_passes_when_value_matches() -> None:
    ctx = _ctx(_FakeMockServer([_make_recorded("/flags/", query_params={"v": "2"})]))

    await AssertFlagsRequestQueryParamAction().execute({"param": "v", "expected": "2"}, ctx)


@pytest.mark.asyncio
async def test_assert_flags_request_query_param_fails_when_value_differs() -> None:
    ctx = _ctx(_FakeMockServer([_make_recorded("/flags/", query_params={"v": "1"})]))

    with pytest.raises(AssertionError, match="expected 'v'='2'|v=.*'2'"):
        await AssertFlagsRequestQueryParamAction().execute({"param": "v", "expected": "2"}, ctx)


@pytest.mark.asyncio
async def test_assert_flags_request_query_param_fails_when_no_flags_request() -> None:
    ctx = _ctx(_FakeMockServer([_make_recorded("/batch", query_params={})]))

    with pytest.raises(AssertionError, match="No /flags requests recorded"):
        await AssertFlagsRequestQueryParamAction().execute({"param": "v", "expected": "2"}, ctx)


@pytest.mark.asyncio
async def test_assert_event_count_with_name_counts_across_batches() -> None:
    ctx = _ctx(
        _FakeMockServer(
            [
                _make_recorded(
                    "/batch",
                    parsed_events=[
                        {"event": "$feature_flag_called", "properties": {}},
                        {"event": "regular_event", "properties": {}},
                    ],
                ),
                _make_recorded(
                    "/batch",
                    parsed_events=[{"event": "$feature_flag_called", "properties": {}}],
                ),
                # /flags request must be ignored even if it ever has parsed_events
                _make_recorded(
                    "/flags/",
                    parsed_events=[{"event": "$feature_flag_called"}],
                ),
            ]
        )
    )

    await AssertEventCountWithNameAction().execute(
        {"name": "$feature_flag_called", "expected": 2}, ctx
    )


@pytest.mark.asyncio
async def test_assert_event_count_with_name_fails_on_mismatch() -> None:
    ctx = _ctx(
        _FakeMockServer(
            [_make_recorded("/batch", parsed_events=[{"event": "$feature_flag_called"}])]
        )
    )

    with pytest.raises(AssertionError, match="Expected 2 events"):
        await AssertEventCountWithNameAction().execute(
            {"name": "$feature_flag_called", "expected": 2}, ctx
        )


@pytest.mark.asyncio
async def test_assert_event_property_in_named_event_finds_first_match() -> None:
    ctx = _ctx(
        _FakeMockServer(
            [
                _make_recorded(
                    "/batch",
                    parsed_events=[
                        {"event": "regular_event", "properties": {"$feature_flag": "wrong"}},
                        {
                            "event": "$feature_flag_called",
                            "properties": {"$feature_flag": "my-flag"},
                        },
                    ],
                )
            ]
        )
    )

    await AssertEventPropertyInNamedEventAction().execute(
        {
            "event_name": "$feature_flag_called",
            "property": "$feature_flag",
            "expected": "my-flag",
        },
        ctx,
    )


@pytest.mark.asyncio
async def test_assert_event_property_in_named_event_fails_when_no_match() -> None:
    ctx = _ctx(
        _FakeMockServer(
            [_make_recorded("/batch", parsed_events=[{"event": "regular_event"}])]
        )
    )

    with pytest.raises(AssertionError, match="No captured event with name"):
        await AssertEventPropertyInNamedEventAction().execute(
            {
                "event_name": "$feature_flag_called",
                "property": "$feature_flag",
                "expected": "my-flag",
            },
            ctx,
        )


@pytest.mark.asyncio
async def test_assert_action_result_compares_scalar_result() -> None:
    ctx = _ctx(_FakeMockServer([]), last_action_result="variant-a")

    await AssertActionResultAction().execute({"expected": "variant-a"}, ctx)


@pytest.mark.asyncio
async def test_assert_action_result_extracts_field_from_dict_result() -> None:
    ctx = _ctx(
        _FakeMockServer([]),
        last_action_result={"success": True, "value": "variant-a"},
    )

    await AssertActionResultAction().execute(
        {"field": "value", "expected": "variant-a"}, ctx
    )


@pytest.mark.asyncio
async def test_assert_action_result_fails_on_mismatch() -> None:
    ctx = _ctx(_FakeMockServer([]), last_action_result="other")

    with pytest.raises(AssertionError, match="Expected last action result 'variant-a'"):
        await AssertActionResultAction().execute({"expected": "variant-a"}, ctx)


@pytest.mark.asyncio
async def test_contract_executor_records_last_action_result_for_non_assertions() -> None:
    """Lock down the executor wiring: action results from non-assertion actions
    are stored on ctx.last_action_result; assertion actions don't overwrite it."""
    contract = ContractExecutor()
    expected_result = {"success": True, "value": "variant-a"}
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(return_value=expected_result))
    ctx = SimpleNamespace(
        sdk_adapter=adapter,
        mock_server=_FakeMockServer([]),
        last_action_result=None,
    )

    await contract.execute_action(
        "get_feature_flag",
        {"key": "k", "distinct_id": "u"},
        ctx,
    )
    assert ctx.last_action_result == expected_result

    # An assertion that passes must not overwrite the recorded result.
    await contract.execute_action(
        "assert_action_result",
        {"field": "value", "expected": "variant-a"},
        ctx,
    )
    assert ctx.last_action_result == expected_result
