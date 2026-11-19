"""Opt-in known-absent property keys: adapter params, suite gating and /flags body assertions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from posthog_test_harness.actions import AssertFlagsRequestFieldAbsentAction, GetFeatureFlagAction
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.suites.contract_suite import ContractTestSuite
from posthog_test_harness.types import FeatureFlagRequest

SUITE = "feature_flags_known_absent"
LOCAL = "feature_flags_local_evaluation_v1"
KNOWN_ABSENT = "feature_flags_known_absent_v1"
FIELDS = ("person_property_keys_known_absent", "group_property_keys_known_absent")
SUITE_ONLY_PARAMS = {*FIELDS, "allow_remote_fallback", "allow_adapter_error"}


@pytest.mark.parametrize(
    "capabilities", [None, [], [LOCAL], [KNOWN_ABSENT], ["feature_flags", "flags_response_v4", KNOWN_ABSENT]]
)
def test_suite_requires_both_capabilities(capabilities):
    suite = ContractTestSuite(SUITE, ContractExecutor())
    assert suite.collect_tests(capabilities=capabilities) == []
    assert suite.collect_tests(capabilities=[LOCAL, KNOWN_ABSENT])


def test_suite_covers_spec_scenarios_without_unsupported_operators():
    collected = ContractTestSuite(SUITE, ContractExecutor()).collect_tests(capabilities=[LOCAL, KNOWN_ABSENT])
    tests = {name.split(".", 1)[1]: test for name, test in collected}
    assert set(tests) == {
        "listed_key_matches_is_not_set",
        "listed_key_does_not_match_is_set",
        "listed_key_overrides_supplied_value",
        "listed_key_operator_results",
        "over_limit_person_keys_make_no_flags_request",
        "group_key_listed_for_other_group_type_falls_back",
        "unlisted_omitted_key_falls_back",
        "fallback_sends_person_keys_known_absent",
        "fallback_sends_group_keys_known_absent",
        "fallback_without_known_absent_input_omits_fields",
    }
    operators = {
        prop["operator"]
        for test in tests.values()
        for step in test["steps"]
        if step["action"] == "configure_local_evaluation_definitions"
        for flag in step["params"]["definitions"]["flags"]
        for group in flag["filters"]["groups"]
        for prop in group["properties"]
    }
    assert not operators & {"icontains_multi", "not_icontains_multi", "between", "not_between"}

    operator_steps = tests["listed_key_operator_results"]["steps"]
    flags = {f["key"]: f for f in operator_steps[0]["params"]["definitions"]["flags"]}
    expected = {}
    for evaluation, assertion in zip(operator_steps, operator_steps[1:]):
        if evaluation["action"] == "get_feature_flag":
            operator = flags[evaluation["params"]["key"]]["filters"]["groups"][0]["properties"][0]["operator"]
            expected[operator] = assertion["params"]["expected"]
    assert expected == {
        "is_not": True,
        "not_icontains": True,
        "not_regex": True,
        "not_starts_with": True,
        "not_ends_with": True,
        "exact": False,
        "icontains": False,
        "gt": False,
        "semver_neq": False,
        "is_date_before": False,
    }

    for name, test in tests.items():
        evaluations = [s["params"] for s in test["steps"] if s["action"] == "get_feature_flag"]
        local_only = name.startswith("listed_key_")
        assert all(bool(e.get("only_evaluate_locally")) is local_only for e in evaluations), name
        assert all(bool(e.get("allow_remote_fallback")) is not local_only for e in evaluations), name
        assert all("force_remote" not in e for e in evaluations), name

    over_limit = tests["over_limit_person_keys_make_no_flags_request"]["steps"]
    evaluation = next(s for s in over_limit if s["action"] == "get_feature_flag")["params"]
    keys = evaluation["person_property_keys_known_absent"]
    assert evaluation["allow_adapter_error"] is True
    assert len(keys) == len(set(keys)) == 1001
    assert [s["action"] for s in over_limit[-2:]] == ["assert_flags_request_count", "assert_no_requests_to_paths"]
    assert over_limit[-2]["params"]["expected"] == 0


def test_known_absent_params_are_not_used_by_other_suites():
    for name, definition in ContractExecutor().get_test_suites().items():
        if name == SUITE:
            continue
        for category in definition.get("categories", {}).values():
            for test in category.get("tests", []):
                for step in test.get("steps", []):
                    assert not SUITE_ONLY_PARAMS & set(step.get("params", {})), (name, test["name"])


def test_client_forwards_known_absent_only_when_set():
    client = SDKAdapterClient("http://adapter")
    assert client._feature_flag_payload(FeatureFlagRequest("k", "u")) == {"key": "k", "distinct_id": "u"}
    payload = client._feature_flag_payload(
        FeatureFlagRequest(
            "k",
            "u",
            person_property_keys_known_absent=[],
            group_property_keys_known_absent={"organization": ["tier"]},
        )
    )
    assert payload["person_property_keys_known_absent"] == []
    assert payload["group_property_keys_known_absent"] == {"organization": ["tier"]}
    assert "allow_remote_fallback" not in payload
    payload = client._feature_flag_payload(FeatureFlagRequest("k", "u", allow_remote_fallback=True))
    assert payload == {"key": "k", "distinct_id": "u", "allow_remote_fallback": True}


@pytest.mark.parametrize("conflict", [{"force_remote": True}, {"only_evaluate_locally": True}])
def test_allow_remote_fallback_conflicts_are_rejected(conflict):
    with pytest.raises(ValueError, match="allow_remote_fallback"):
        FeatureFlagRequest("k", "u", allow_remote_fallback=True, **conflict)
    FeatureFlagRequest("k", "u", allow_remote_fallback=False, **conflict)


@pytest.mark.asyncio
async def test_get_feature_flag_action_passes_known_absent_params():
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(return_value={"success": True, "value": True}))
    ctx = SimpleNamespace(sdk_adapter=adapter, mock_server=MockServerState())
    await GetFeatureFlagAction().execute(
        {
            "key": "k",
            "distinct_id": "u",
            "person_property_keys_known_absent": ["plan"],
            "group_property_keys_known_absent": {"organization": ["tier"]},
            "allow_remote_fallback": True,
        },
        ctx,
    )
    request = adapter.get_feature_flag.await_args.args[0]
    assert request.allow_remote_fallback is True
    assert request.person_property_keys_known_absent == ["plan"]
    assert request.group_property_keys_known_absent == {"organization": ["tier"]}
    await GetFeatureFlagAction().execute({"key": "k", "distinct_id": "u"}, ctx)
    request = adapter.get_feature_flag.await_args.args[0]
    assert request.person_property_keys_known_absent is None
    assert request.group_property_keys_known_absent is None
    assert request.allow_remote_fallback is None


def _adapter_error():
    return aiohttp.ClientResponseError(request_info=None, history=(), status=500)


@pytest.mark.asyncio
async def test_allow_adapter_error_turns_http_error_into_failed_result():
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(side_effect=_adapter_error()))
    ctx = SimpleNamespace(sdk_adapter=adapter, mock_server=MockServerState())
    params = {"key": "k", "distinct_id": "u", "allow_remote_fallback": True}
    with pytest.raises(aiohttp.ClientResponseError):
        await GetFeatureFlagAction().execute(params, ctx)
    result = await GetFeatureFlagAction().execute({**params, "allow_adapter_error": True}, ctx)
    assert result["success"] is False and "500" in result["error"]


def _ctx_with_flags_body(body):
    state = MockServerState()
    if body is not None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        state.record_request("POST", "/flags/", {}, {"v": "2"}, raw)
    return SimpleNamespace(mock_server=state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,message",
    [
        ({"distinct_id": "u"}, None),
        ({"distinct_id": "u", FIELDS[0]: None, FIELDS[1]: None}, None),
        ({"distinct_id": "u", FIELDS[0]: ["plan"]}, "should not contain"),
        ({"distinct_id": "u", FIELDS[1]: {}}, "should not contain"),
        (None, "No /flags requests"),
        (b"not json", "not valid JSON"),
        ([], "not a JSON object"),
    ],
)
async def test_assert_flags_request_field_absent(body, message):
    action = AssertFlagsRequestFieldAbsentAction()
    ctx = _ctx_with_flags_body(body)
    if message is None:
        await action.execute({"fields": list(FIELDS)}, ctx)
    else:
        with pytest.raises(AssertionError, match=message):
            await action.execute({"fields": list(FIELDS)}, ctx)
