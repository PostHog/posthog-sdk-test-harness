from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import GetFeatureFlagAction
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.types import FeatureFlagRequest


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
