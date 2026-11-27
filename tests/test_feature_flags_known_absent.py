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
SUITE_ONLY_PARAMS = {*FIELDS, "allow_remote_fallback"}
UNSUPPORTED_OPERATORS = {"icontains_multi", "not_icontains_multi", "between", "not_between"}
UNSUPPORTED_OPERATOR_TEST = "unsupported_operator_on_listed_key_falls_back"


@pytest.mark.parametrize(
    "capabilities", [None, [], [LOCAL], [KNOWN_ABSENT], ["feature_flags", "flags_response_v4", KNOWN_ABSENT]]
)
def test_suite_requires_both_capabilities(capabilities):
    suite = ContractTestSuite(SUITE, ContractExecutor())
    assert suite.collect_tests(capabilities=capabilities) == []
    assert suite.collect_tests(capabilities=[LOCAL, KNOWN_ABSENT])


def _definition_operators(test):
    def leaves(node):
        if "values" in node:
            for child in node["values"]:
                yield from leaves(child)
        else:
            yield node

    for step in test["steps"]:
        if step["action"] != "configure_local_evaluation_definitions":
            continue
        definitions = step["params"]["definitions"]
        for flag in definitions["flags"]:
            for group in flag["filters"]["groups"]:
                for prop in group["properties"]:
                    yield prop.get("operator")
        for cohort in definitions.get("cohorts", {}).values():
            for leaf in leaves(cohort):
                yield leaf.get("operator")


def test_suite_covers_spec_scenarios():
    collected = ContractTestSuite(SUITE, ContractExecutor()).collect_tests(capabilities=[LOCAL, KNOWN_ABSENT])
    tests = {name.split(".", 1)[1]: test for name, test in collected}
    assert set(tests) == {
        "listed_key_matches_is_not_set",
        "listed_key_does_not_match_is_set",
        "listed_key_overrides_supplied_value",
        "listed_key_operator_results",
        "default_getter_resolves_listed_person_key_locally",
        "default_getter_resolves_listed_group_key_locally",
        "group_key_listed_for_other_group_type_falls_back",
        "unlisted_omitted_key_falls_back",
        "fallback_sends_person_keys_known_absent",
        "fallback_sends_group_keys_known_absent",
        "fallback_without_known_absent_input_omits_fields",
        UNSUPPORTED_OPERATOR_TEST,
    }
    for name, test in tests.items():
        if name != UNSUPPORTED_OPERATOR_TEST:
            assert not set(_definition_operators(test)) & UNSUPPORTED_OPERATORS, name

    unsupported = tests[UNSUPPORTED_OPERATOR_TEST]["steps"]
    assert set(_definition_operators(tests[UNSUPPORTED_OPERATOR_TEST])) & UNSUPPORTED_OPERATORS
    evaluation = next(s for s in unsupported if s["action"] == "get_feature_flag")["params"]
    assert evaluation["allow_remote_fallback"] is True
    assert evaluation["person_property_keys_known_absent"] == ["plan"]
    assert [s["params"] for s in unsupported if s["action"] == "assert_flags_request_count"] == [{"expected": 1}]

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

    group_local = tests["default_getter_resolves_listed_group_key_locally"]["steps"]
    evaluation = next(s for s in group_local if s["action"] == "get_feature_flag")["params"]
    assert evaluation["group_property_keys_known_absent"] == {"organization": ["tier"]}
    assert evaluation["group_properties"]["organization"]["tier"] == "enterprise"
    assert [s["action"] for s in group_local[-2:]] == ["assert_flags_request_count", "assert_no_requests_to_paths"]
    assert group_local[-2]["params"]["expected"] == 0

    person_local = tests["default_getter_resolves_listed_person_key_locally"]["steps"]
    evaluation = next(s for s in person_local if s["action"] == "get_feature_flag")["params"]
    assert evaluation["person_property_keys_known_absent"] == ["plan"]
    assert "plan" not in evaluation.get("person_properties", {})
    assert [s["params"] for s in person_local if s["action"] == "assert_action_result"] == [
        {"field": "success", "expected": True},
        {"field": "value", "expected": True},
    ]
    assert [s["action"] for s in person_local[-2:]] == ["assert_flags_request_count", "assert_no_requests_to_paths"]
    assert person_local[-2]["params"]["expected"] == 0


def test_known_absent_params_are_not_used_by_other_suites():
    for name, definition in ContractExecutor().get_test_suites().items():
        if name == SUITE:
            continue
        # This suite's capability also requires allow_remote_fallback; known-absent keys need a test-level requires.
        unsupported_types = name == "feature_flags_unsupported_filter_types"
        for category in definition.get("categories", {}).values():
            for test in category.get("tests", []):
                allowed = set()
                if unsupported_types:
                    allowed.add("allow_remote_fallback")
                    requires = test.get("requires", [])
                    if KNOWN_ABSENT in ([requires] if isinstance(requires, str) else requires):
                        allowed.update(FIELDS)
                for step in test.get("steps", []):
                    assert not (SUITE_ONLY_PARAMS - allowed) & set(step.get("params", {})), (name, test["name"])


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
    payload = client._feature_flag_payload(FeatureFlagRequest("k", "u", group_property_keys_known_absent={}))
    assert payload == {"key": "k", "distinct_id": "u", "group_property_keys_known_absent": {}}
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


def _adapter_error(status=500):
    return aiohttp.ClientResponseError(request_info=None, history=(), status=status)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 500])
async def test_adapter_http_errors_fail_the_step(status):
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(side_effect=_adapter_error(status)))
    ctx = SimpleNamespace(sdk_adapter=adapter, mock_server=MockServerState())
    params = {"key": "k", "distinct_id": "u", "allow_remote_fallback": True, "allow_adapter_error": True}
    with pytest.raises(aiohttp.ClientResponseError):
        await GetFeatureFlagAction().execute(params, ctx)


def _ctx_with_flags_body(body):
    state = MockServerState()
    state.record_request("POST", "/flags/other", {}, {}, b"{}")
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


UNUSABLE_GROUP_KEY = "feature_flags_unusable_group_key_v1"
JSON_GROUP_KEYS = "feature_flags_json_group_keys_v1"
UNUSABLE_GROUP_KEY_CATEGORY = "unusable_group_key"
EMPTY_STRING_GROUP_KEY_TESTS = {
    "omitted_group_resolves_group_flag_false",
    "empty_string_group_key_resolves_group_flag_false",
    "empty_string_group_key_skips_mixed_group_condition",
    "group_dependency_without_usable_key_resolves_false",
}
JSON_GROUP_KEY_TESTS = {
    "null_group_key_resolves_group_flag_false": None,
    "boolean_group_key_resolves_group_flag_false": True,
    "array_group_key_resolves_group_flag_false": [],
    "object_group_key_resolves_group_flag_false": {},
}


def _unusable_group_key_tests(capabilities):
    collected = ContractTestSuite(SUITE, ContractExecutor()).collect_tests(capabilities=capabilities)
    return {
        name.split(".", 1)[1]: test for name, test in collected if name.split(".", 1)[0] == UNUSABLE_GROUP_KEY_CATEGORY
    }


@pytest.mark.parametrize(
    "capabilities,expected",
    [
        ([LOCAL, KNOWN_ABSENT], set()),
        ([LOCAL, KNOWN_ABSENT, JSON_GROUP_KEYS], set()),
        ([LOCAL, UNUSABLE_GROUP_KEY, JSON_GROUP_KEYS], set()),
        ([LOCAL, KNOWN_ABSENT, UNUSABLE_GROUP_KEY], EMPTY_STRING_GROUP_KEY_TESTS),
        (
            [LOCAL, KNOWN_ABSENT, UNUSABLE_GROUP_KEY, JSON_GROUP_KEYS],
            EMPTY_STRING_GROUP_KEY_TESTS | set(JSON_GROUP_KEY_TESTS),
        ),
    ],
)
def test_unusable_group_key_tests_are_opt_in(capabilities, expected):
    assert set(_unusable_group_key_tests(capabilities)) == expected


def test_unusable_group_key_tests_resolve_false_without_flags_requests():
    tests = _unusable_group_key_tests([LOCAL, KNOWN_ABSENT, UNUSABLE_GROUP_KEY, JSON_GROUP_KEYS])
    for name, test in tests.items():
        steps = test["steps"]
        evaluations = [s["params"] for s in steps if s["action"] == "get_feature_flag"]
        assert any(e.get("only_evaluate_locally") for e in evaluations), name
        assert any(e.get("allow_remote_fallback") for e in evaluations), name
        assert all("force_remote" not in e for e in evaluations), name
        assert [s["action"] for s in steps[-2:]] == ["assert_flags_request_count", "assert_no_requests_to_paths"], name
        assert steps[-2]["params"]["expected"] == 0, name
        if name != "omitted_group_resolves_group_flag_false":
            key_kinds = {repr(e["groups"]["company"]) for e in evaluations if "groups" in e}
            expected_key = JSON_GROUP_KEY_TESTS.get(name, "")
            assert key_kinds == {repr(expected_key)}, name

    omitted = [
        s["params"]
        for s in tests["omitted_group_resolves_group_flag_false"]["steps"]
        if s["action"] == "get_feature_flag"
    ]
    assert all("groups" not in e for e in omitted)


@pytest.mark.asyncio
@pytest.mark.parametrize("group_key", ["", None, True, [], {}])
async def test_get_feature_flag_action_passes_unusable_group_keys_unchanged(group_key):
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(return_value={"success": True, "value": False}))
    ctx = SimpleNamespace(sdk_adapter=adapter, mock_server=MockServerState())
    await GetFeatureFlagAction().execute({"key": "k", "distinct_id": "u", "groups": {"company": group_key}}, ctx)
    request = adapter.get_feature_flag.await_args.args[0]
    payload = SDKAdapterClient("http://adapter")._feature_flag_payload(request)
    assert payload["groups"] == {"company": group_key}
