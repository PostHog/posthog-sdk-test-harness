"""Opt-in local-evaluation protocol and definitions mock regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import GetFeatureFlagAction
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.suites.contract_suite import ContractTestSuite
from posthog_test_harness.types import FeatureFlagRequest

CAPABILITY = "feature_flags_local_evaluation_v1"
PATHS = [
    "/flags/definitions",
    "/flags/definitions/",
    "/api/feature_flag/local_evaluation",
    "/api/feature_flag/local_evaluation/",
]


@pytest.mark.parametrize("path", PATHS)
def test_definitions_get_has_real_envelope_and_separate_accounting(path):
    state = MockServerState()
    client = MockServer(state).app.test_client()
    response = client.get(
        path, query_string={"token": "phc_test_key"}, headers={"Authorization": "Bearer phx_test_key"}
    )
    assert response.status_code == 200
    assert response.get_json() == {"flags": [], "cohorts": {}, "group_type_mapping": {}}
    assert state.get_requests() == []
    assert len(state.get_definition_requests()) == 1


def test_local_only_is_optional_and_conflicts_are_rejected():
    client = SDKAdapterClient("http://adapter")
    assert client._feature_flag_payload(FeatureFlagRequest("k", "u")) == {"key": "k", "distinct_id": "u"}
    assert (
        client._feature_flag_payload(FeatureFlagRequest("k", "u", only_evaluate_locally=True))["only_evaluate_locally"]
        is True
    )
    with pytest.raises(ValueError, match="force_remote"):
        FeatureFlagRequest("k", "u", only_evaluate_locally=True, force_remote=True)


@pytest.mark.parametrize(
    "capabilities", [None, [], ["capture_v0", "feature_flags"], ["feature_flags_local_evaluation"]]
)
def test_old_capabilities_skip_local_suite(capabilities):
    executor = ContractExecutor()
    assert "feature_flags_local_evaluation" in executor.get_test_suites()
    suite = ContractTestSuite("feature_flags_local_evaluation", executor)
    assert suite.collect_tests(capabilities=capabilities) == []
    assert suite.collect_tests(capabilities=[CAPABILITY])


@pytest.mark.asyncio
async def test_local_only_rejects_remote_fallback_even_with_correct_value():
    state = MockServerState()

    async def fallback(request):
        state.record_request("POST", "/flags/", {}, {}, b"{}")
        return {"success": True, "value": True, "locally_evaluated": True}

    ctx = SimpleNamespace(
        sdk_adapter=SimpleNamespace(get_feature_flag=AsyncMock(side_effect=fallback)), mock_server=state
    )
    with pytest.raises(AssertionError, match="remote"):
        await GetFeatureFlagAction().execute({"key": "k", "distinct_id": "u", "only_evaluate_locally": True}, ctx)


@pytest.mark.parametrize("test_id", [None, "partition-a"])
def test_definitions_snapshot_auth_queue_and_reset_isolation(test_id):
    from posthog_test_harness.mock_server.scoped import ScopedMockServerState
    from posthog_test_harness.types import MockResponse

    state = MockServerState()
    scoped = ScopedMockServerState(state, test_id) if test_id else state
    definitions = {
        "flags": [{"key": "k"}],
        "cohorts": {"1": {"type": "AND", "values": []}},
        "group_type_mapping": {"0": "company"},
        "property_matching_version": 2,
    }
    scoped.set_definitions(definitions, api_key="project", personal_api_key="personal")
    scoped.set_response_queue([MockResponse(status_code=503)])
    definitions["flags"].clear()  # Configuration is a snapshot, not a mutable reference.
    headers = {"Authorization": "Bearer personal"}
    if test_id:
        headers["X-Test-Id"] = test_id
    client = MockServer(state).app.test_client()
    response = client.get(PATHS[0], query_string={"token": "project"}, headers=headers)
    assert response.status_code == 200
    assert response.json["flags"] == [{"key": "k"}]
    assert response.json["cohorts"] == definitions["cohorts"]
    assert response.json["group_type_mapping"] == {"0": "company"}
    assert response.json["property_matching_version"] == 2
    assert scoped.get_requests() == []
    assert scoped.get_definition_requests()[0].parsed_events is None
    for bad_headers, query in [(headers, {}), ({**headers, "Authorization": "Bearer wrong"}, {"token": "project"})]:
        assert client.get(PATHS[0], query_string=query, headers=bad_headers).status_code == 401
    assert client.post("/batch", json={"event": "test"}, headers=headers).status_code == 503
    other_headers = {"Authorization": "Bearer phx_test_key", "X-Test-Id": "partition-b"}
    other = client.get(PATHS[0], query_string={"token": "phc_test_key"}, headers=other_headers)
    assert "property_matching_version" not in other.json
    scoped.set_definitions({"flags": [{"key": "k"}]})
    default_headers = {**headers, "Authorization": "Bearer phx_test_key"}
    response = client.get(PATHS[0], query_string={"token": "phc_test_key"}, headers=default_headers)
    assert "property_matching_version" not in response.json
    scoped.reset()
    assert scoped.get_definition_requests() == []
    assert len(state.get_definition_requests("partition-b")) == 1
    assert client.get(PATHS[0], query_string={"token": "phc_test_key"}, headers=default_headers).json["flags"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities", [None, [], ["capture_v0", "capture_ai_v0"], ["feature_flags_local_evaluation"]]
)
@pytest.mark.parametrize("parallel", [False, True])
async def test_runner_without_opt_in_executes_no_local_actions(capabilities, parallel):
    from posthog_test_harness.tests.context import TestContext as HarnessContext
    from posthog_test_harness.tests.runner import run_all_suites

    adapter = SDKAdapterClient("http://must-not-be-contacted.invalid")
    adapter.reset = AsyncMock(side_effect=AssertionError("No new adapter calls allowed"))
    ctx = HarnessContext(adapter, MockServerState(), "http://mock")
    result = await run_all_suites(
        ctx,
        suite_names=["feature_flags_local_evaluation"],
        capabilities=capabilities,
        concurrency=2 if parallel else 1,
        supports_parallel=parallel,
    )
    assert result.total == 0
    adapter.reset.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"success": True, "value": True},
        {"success": True, "value": True, "locally_evaluated": False},
        {"success": False, "value": False, "locally_evaluated": True},
        {"success": True, "value": None, "locally_evaluated": True},
    ],
)
async def test_local_only_requires_conclusive_local_result(response):
    ctx = SimpleNamespace(
        sdk_adapter=SimpleNamespace(get_feature_flag=AsyncMock(return_value=response)), mock_server=MockServerState()
    )
    with pytest.raises(AssertionError, match="Local-only"):
        await GetFeatureFlagAction().execute({"key": "k", "distinct_id": "u", "only_evaluate_locally": True}, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/flags", "/flags/", "/decide", "/decide/"])
async def test_local_only_detects_fallback_http_paths(path):
    state = MockServerState()
    MockServer(state).app.test_client().post(path, json={"token": "phc_test_key"})
    ctx = SimpleNamespace(
        sdk_adapter=SimpleNamespace(
            get_feature_flag=AsyncMock(return_value={"success": True, "value": False, "locally_evaluated": True})
        ),
        mock_server=state,
    )
    with pytest.raises(AssertionError, match="remote"):
        await GetFeatureFlagAction().execute({"key": "k", "distinct_id": "u", "only_evaluate_locally": True}, ctx)


@pytest.mark.asyncio
async def test_reload_is_bounded_and_requires_fresh_successful_get():
    import asyncio

    from posthog_test_harness.actions import ReloadFeatureFlagDefinitionsAction

    state = MockServerState()
    adapter = SimpleNamespace(reload_feature_flag_definitions=AsyncMock(return_value={"success": True, "ready": True}))
    ctx = SimpleNamespace(sdk_adapter=adapter, mock_server=state)
    action = ReloadFeatureFlagDefinitionsAction()
    # A ready claim (or a prior GET) is not proof of a fresh fetch.
    state.record_definition_request(PATHS[0], {"authorization": "Bearer phx_test_key"}, {"token": "phc_test_key"})
    with pytest.raises(AssertionError, match="fresh"):
        await action.execute({}, ctx)
    for timeout in [0, -1, 30001]:
        with pytest.raises(ValueError, match="timeout_ms"):
            await action.execute({"timeout_ms": timeout}, ctx)

    async def hang(timeout_ms):
        await asyncio.sleep(10)

    adapter.reload_feature_flag_definitions.side_effect = hang
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(action.execute({"timeout_ms": 10}, ctx), timeout=1)

    async def not_ready(timeout_ms):
        return {"success": True, "ready": False}

    adapter.reload_feature_flag_definitions.side_effect = not_ready
    with pytest.raises(AssertionError, match="ready"):
        await action.execute({}, ctx)


@pytest.mark.asyncio
async def test_reload_preserves_evaluation_result_and_checks_scoped_get():
    from posthog_test_harness.mock_server.scoped import ScopedMockServerState

    state = MockServerState()
    scoped = ScopedMockServerState(state, "a")

    async def reload(timeout_ms):
        state.record_definition_request(
            PATHS[0], {"authorization": "Bearer phx_test_key", "x-test-id": "a"}, {"token": "phc_test_key"}
        )
        return {"success": True, "ready": True}

    ctx = SimpleNamespace(
        sdk_adapter=SimpleNamespace(reload_feature_flag_definitions=AsyncMock(side_effect=reload)),
        mock_server=scoped,
        last_action_result={"value": False},
    )
    await ContractExecutor().execute_action("reload_feature_flag_definitions", {}, ctx)
    assert ctx.last_action_result == {"value": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_optional_serialization_and_reload_endpoint(monkeypatch, scoped):
    from posthog_test_harness.sdk_adapter.client import ScopedSDKAdapterClient
    from posthog_test_harness.types import InitRequest
    from tests.test_feature_flag_support import _FakeSession

    captured = {}

    def session(**kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeSession(captured)

    monkeypatch.setattr("posthog_test_harness.sdk_adapter.client.aiohttp.ClientSession", session)
    base = SDKAdapterClient("http://adapter")
    client = ScopedSDKAdapterClient(base, "test-123") if scoped else base
    suffix = "?test_id=test-123" if scoped else ""
    await client.init(InitRequest("project", "http://mock"))
    assert captured["json"] == {"api_key": "project", "host": "http://mock"}
    await client.init(InitRequest("project", "http://mock", personal_api_key="personal"))
    assert captured["json"] == {"api_key": "project", "host": "http://mock", "personal_api_key": "personal"}
    assert captured["url"] == "http://adapter/init" + suffix
    await client.get_feature_flag(FeatureFlagRequest("k", "u"))
    assert captured["json"] == {"key": "k", "distinct_id": "u"}
    await client.get_feature_flag(FeatureFlagRequest("k", "u", only_evaluate_locally=True))
    assert captured["json"] == {"key": "k", "distinct_id": "u", "only_evaluate_locally": True}
    await client.reload_feature_flag_definitions(321)
    assert captured["url"] == "http://adapter/reload_feature_flag_definitions" + suffix
    assert captured["json"] == {"timeout_ms": 321}
    assert captured["timeout"].total == 0.321


def test_optional_interface_method_is_not_abstract_and_health_does_not_opt_in():
    from posthog_test_harness.sdk_adapter.interface import SDKAdapterInterface
    from posthog_test_harness.types import HealthResponse

    assert "reload_feature_flag_definitions" not in SDKAdapterInterface.__abstractmethods__
    assert CAPABILITY not in HealthResponse("sdk", "1", "1").capabilities


def test_fixture_versions_vectors_and_version_only_definitions():
    suite = ContractTestSuite("feature_flags_local_evaluation", ContractExecutor())
    tests = suite.collect_tests(capabilities=[CAPABILITY])
    assert len(tests) == 4
    for (_, test), version in zip(tests[:3], [None, 1, 2]):
        definitions = test["steps"][0]["params"]["definitions"]
        assert definitions.get("property_matching_version") == version
        assert all(flag["version"] == 2 for flag in definitions["flags"])
        expected = [False, False, True, False, True, True] if version == 2 else [True, True, False, True, True, True]
        assertions = [s["params"]["expected"] for s in test["steps"] if s["action"] == "assert_action_result"]
        assert assertions[:12] == [value for exact in expected for value in (exact, not exact)]
        evaluations = [s for s in test["steps"] if s["action"] == "get_feature_flag"]
        assert all(s["params"]["only_evaluate_locally"] is True for s in evaluations)
        assert definitions["cohorts"] and definitions["group_type_mapping"]
    snapshots = [
        s["params"]["definitions"]
        for s in tests[3][1]["steps"]
        if s["action"] == "configure_local_evaluation_definitions"
    ]
    assert [s.get("property_matching_version") for s in snapshots] == [1, 2, 1, 2, None]
    assert all(
        {k: v for k, v in s.items() if k != "property_matching_version"}
        == {k: v for k, v in snapshots[0].items() if k != "property_matching_version"}
        for s in snapshots
    )


@pytest.mark.asyncio
async def test_failed_definitions_get_cannot_satisfy_readiness():
    from posthog_test_harness.actions import ReloadFeatureFlagDefinitionsAction

    state = MockServerState()

    async def reload(timeout_ms):
        state.record_definition_request(PATHS[0], {}, {"token": "phc_test_key"})
        return {"success": True, "ready": True}

    ctx = SimpleNamespace(
        sdk_adapter=SimpleNamespace(reload_feature_flag_definitions=AsyncMock(side_effect=reload)),
        mock_server=state,
    )
    with pytest.raises(AssertionError, match="fresh successful"):
        await ReloadFeatureFlagDefinitionsAction().execute({}, ctx)


def test_new_controls_are_not_used_by_any_existing_suite():
    executor = ContractExecutor()
    for name, definition in executor.get_test_suites().items():
        if name == "feature_flags_local_evaluation":
            assert definition["requires"] == CAPABILITY
            continue
        for category in definition.get("categories", {}).values():
            for test in category.get("tests", []):
                for step in test.get("steps", []):
                    assert step["action"] not in {
                        "reload_feature_flag_definitions",
                        "configure_local_evaluation_definitions",
                    }
                    assert "personal_api_key" not in step.get("params", {})
                    assert "only_evaluate_locally" not in step.get("params", {})
