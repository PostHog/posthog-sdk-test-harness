import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import GetFeatureFlagAction
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import ScopedSDKAdapterClient, SDKAdapterClient
from posthog_test_harness.types import FeatureFlagRequest, MockResponse


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


def test_feature_flag_contract_keeps_top_level_action_distinct_id_without_requiring_top_level_flags_field() -> None:
    contract = ContractExecutor()
    feature_flag_test = contract.get_test_suites()["feature_flags"]["categories"]["request_payload"]["tests"][0]

    get_feature_flag_step = next(step for step in feature_flag_test["steps"] if step["action"] == "get_feature_flag")
    assert get_feature_flag_step["params"]["distinct_id"] == "test_user_123"

    assert not any(
        step["action"] == "assert_flags_request_field" and step["params"]["field"] == "distinct_id"
        for step in feature_flag_test["steps"]
    )
