"""Initialization identity must reach capable adapters before flag evaluation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from posthog_test_harness.actions import InitAction
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.context import TestContext
from posthog_test_harness.tests.runner import run_all_suites


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", [None, "bootstrap-test"])
@pytest.mark.parametrize(
    "capabilities,identity,expected_identity",
    [
        ([], "user-café 雪", None),
        (["bootstrap_identity"], "user-café 雪", "user-café 雪"),
        (["bootstrap_identity"], None, None),
    ],
)
async def test_init_identity_over_http(test_id, capabilities, identity, expected_identity):
    requests = []

    async def initialize(request):
        requests.append((dict(request.query), await request.json()))
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/init", initialize)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        adapter = SDKAdapterClient(f"http://127.0.0.1:{port}")
        ctx = TestContext(
            adapter,
            MockServerState(),
            "http://127.0.0.1:8081",
            test_id=test_id,
            capabilities=capabilities,
        )
        params = {"api_key": "phc_bootstrap_fixture"}
        if identity is not None:
            params["distinct_id"] = identity
        assert await InitAction().execute(params, ctx) == {"success": True}
        expected = {"api_key": "phc_bootstrap_fixture", "host": ctx.mock_server_url}
        if expected_identity is not None:
            expected["distinct_id"] = expected_identity
        assert requests == [({"test_id": test_id} if test_id else {}, expected)]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
async def test_runner_propagates_bootstrap_capability(parallel):
    adapter = SDKAdapterClient("http://127.0.0.1:8080")
    ctx = TestContext(adapter, MockServerState(), "http://127.0.0.1:8081")
    observed = []

    async def run_test(test_def, test_ctx):
        observed.append((test_ctx.capabilities, test_ctx.test_id))

    with patch("posthog_test_harness.tests.runner.ContractExecutor") as constructor:
        contract = MagicMock()
        contract.get_test_suites.return_value = {
            "flags": {"categories": {"identity": {"tests": [{"name": "bootstrap", "steps": []}]}}}
        }
        contract.run_test = AsyncMock(side_effect=run_test)
        constructor.return_value = contract
        result = await run_all_suites(
            ctx,
            concurrency=2 if parallel else 1,
            supports_parallel=parallel,
            capabilities=["bootstrap_identity"],
        )
    assert result.total == result.passed == 1
    assert observed[0][0] == {"bootstrap_identity"}
    assert (observed[0][1] is not None) == parallel


def test_remote_flag_cases_declare_identity_at_initialization():
    suite = ContractExecutor().get_test_suites()["feature_flags"]
    checked = 0
    for category in suite["categories"].values():
        for test in category["tests"]:
            initial_identity = None
            for step in test["steps"]:
                if step["action"] == "init":
                    initial_identity = step.get("params", {}).get("distinct_id")
                elif step["action"] == "get_feature_flag":
                    assert initial_identity == step["params"]["distinct_id"], test["name"]
                    checked += 1
    assert checked > 0
