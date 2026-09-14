"""Client flag loading and cached reads are distinct from server evaluation."""

from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.context import TestContext
from posthog_test_harness.tests.suites.contract_suite import ContractTestSuite
from posthog_test_harness.types import MockResponse


CAPABILITIES = ["bootstrap_identity", "client_feature_flags"]


def test_client_and_server_flag_selection():
    suite = ContractTestSuite("feature_flags", ContractExecutor())
    client = suite.collect_tests("client", CAPABILITIES)
    server = suite.collect_tests("server", CAPABILITIES)
    assert len(client) == 10
    assert len(server) == 17
    assert not ({name for name, _ in client} & {name for name, _ in server})
    assert suite.collect_tests("client", []) == []
    assert suite.collect_tests("client", ["bootstrap_identity"]) == []
    assert suite.collect_tests("client", ["client_feature_flags"]) == []
    assert server == suite.collect_tests("server", [])
    for _, test in server:
        for step in test["steps"]:
            if step["action"] == "init":
                assert "distinct_id" not in step.get("params", {})
            if step["action"] == "get_feature_flag":
                assert step["params"]["distinct_id"]
    for _, test in client:
        assert test["sdk_types"] == ["client"]
        initialized = False
        loaded = False
        for step in test["steps"]:
            if step["action"] == "init":
                assert step["params"]["distinct_id"]
                initialized = True
            elif step["action"] == "reload_feature_flags":
                assert initialized
                loaded = True
            elif step["action"] == "get_cached_feature_flag":
                assert loaded
                assert set(step["params"]) == {"key"}
            assert step["action"] != "get_feature_flag"


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", [None, "client-flags-test"])
async def test_client_flag_operations_over_http(test_id):
    requests = []

    async def handle(request):
        requests.append((request.path, dict(request.query), await request.json()))
        if request.path == "/get_cached_feature_flag":
            return web.json_response({"success": True, "value": "variant-a"})
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/reload_feature_flags", handle)
    app.router.add_post("/get_cached_feature_flag", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        adapter = SDKAdapterClient(f"http://127.0.0.1:{runner.addresses[0][1]}")
        ctx = TestContext(adapter, MockServerState(), "http://127.0.0.1:8081", test_id=test_id)
        assert await ctx.sdk_adapter.reload_feature_flags() == {"success": True}
        assert await ctx.sdk_adapter.get_cached_feature_flag("flag-café 雪") == {"success": True, "value": "variant-a"}
        query = {"test_id": test_id} if test_id else {}
        assert requests == [
            ("/reload_feature_flags", query, {}),
            ("/get_cached_feature_flag", query, {"key": "flag-café 雪"}),
        ]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("response,fresh,status", [(False, True, 200), (True, False, 200), (True, True, 502)])
async def test_reload_rejects_failed_or_stale_load(response, fresh, status):
    from posthog_test_harness.actions import ReloadFeatureFlagsAction

    ctx = TestContext(AsyncMock(), MockServerState(), "http://127.0.0.1:8081", capabilities=CAPABILITIES)

    def record():
        ctx.mock_server.set_response_queue([MockResponse(status_code=status)])
        ctx.mock_server.record_request("POST", "/flags/", {}, {}, b"{}")

    record()

    async def reload():
        if fresh:
            record()
        return {"success": response}

    ctx.sdk_adapter.reload_feature_flags = AsyncMock(side_effect=reload)
    with pytest.raises(AssertionError):
        await ReloadFeatureFlagsAction().execute({}, ctx)


@pytest.mark.asyncio
async def test_cached_read_records_sdk_value_and_rejects_unsuccessful_result():
    from posthog_test_harness.actions import GetCachedFeatureFlagAction

    ctx = TestContext(AsyncMock(), MockServerState(), "http://127.0.0.1:8081", capabilities=CAPABILITIES)
    action = GetCachedFeatureFlagAction()
    assert action.records_result
    for value in [False, "variant-a", None]:
        ctx.sdk_adapter.get_cached_feature_flag.return_value = {"success": True, "value": value}
        assert await action.execute({"key": "flag"}, ctx) == {"success": True, "value": value}
    for result in [{"success": False, "value": True}, {"success": True}, {"success": True, "value": 0}]:
        ctx.sdk_adapter.get_cached_feature_flag.return_value = result
        with pytest.raises(AssertionError):
            await action.execute({"key": "flag"}, ctx)
