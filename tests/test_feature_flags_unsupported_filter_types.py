"""Opt-in unsupported property-filter types: suite gating, contents and the expect_inconclusive step param."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from posthog_test_harness.actions import GetFeatureFlagAction
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.sdk_adapter.client import SDKAdapterClient
from posthog_test_harness.tests.suites.contract_suite import ContractTestSuite

SUITE = "feature_flags_unsupported_filter_types"
LOCAL = "feature_flags_local_evaluation_v1"
UNSUPPORTED = "feature_flags_unsupported_filter_types_v1"
KNOWN_ABSENT = "feature_flags_known_absent_v1"
KNOWN_ABSENT_TEST = "person_metadata_cohort_leaf.local_only_inconclusive_with_created_at_known_absent"
REASONS = "feature_flags_unresolved_reasons_v1"
REASON_TESTS = {
    "unresolved_reason.person_metadata_cohort_leaf_reports_unsupported_definition",
    "unresolved_reason.person_metadata_without_properties_reports_unsupported_definition",
    "unresolved_reason.unknown_condition_type_reports_unsupported_definition",
}
SUPPORTED_TYPES = {"person", "group", "cohort", "flag", None, ""}


def _collect(capabilities):
    return dict(ContractTestSuite(SUITE, ContractExecutor()).collect_tests(capabilities=capabilities))


@pytest.mark.parametrize(
    "capabilities", [None, [], [LOCAL], [UNSUPPORTED], [UNSUPPORTED, KNOWN_ABSENT], ["feature_flags", UNSUPPORTED]]
)
def test_suite_requires_both_capabilities(capabilities):
    assert _collect(capabilities) == {}
    assert _collect([LOCAL, UNSUPPORTED])


def test_known_absent_case_requires_its_own_capability():
    without = _collect([LOCAL, UNSUPPORTED])
    with_known_absent = _collect([LOCAL, UNSUPPORTED, KNOWN_ABSENT])
    assert set(with_known_absent) - set(without) == {KNOWN_ABSENT_TEST}
    for name, test in with_known_absent.items():
        uses_known_absent = any(
            "person_property_keys_known_absent" in s.get("params", {})
            or "group_property_keys_known_absent" in s.get("params", {})
            for s in test["steps"]
        )
        assert uses_known_absent is (name == KNOWN_ABSENT_TEST), name


def _evaluations(test):
    return [s["params"] for s in test["steps"] if s["action"] == "get_feature_flag"]


def _flags_count(test):
    return [s["params"]["expected"] for s in test["steps"] if s["action"] == "assert_flags_request_count"]


def _results(test):
    return [s["params"] for s in test["steps"] if s["action"] == "assert_action_result"]


def _filter_types(test):
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
                    yield prop.get("type")
        for cohort in definitions["cohorts"].values():
            for leaf in leaves(cohort):
                yield leaf.get("type")


def test_reason_cases_require_their_own_capability():
    without = _collect([LOCAL, UNSUPPORTED])
    with_reasons = _collect([LOCAL, UNSUPPORTED, REASONS])
    assert set(with_reasons) - set(without) == REASON_TESTS
    for name, test in with_reasons.items():
        reasons = [e.get("expected_unresolved_reason") for e in _evaluations(test)]
        if name in REASON_TESTS:
            assert reasons == ["unsupported_definition"], name
            assert [e.get("expect_inconclusive") for e in _evaluations(test)] == [True], name
            assert _flags_count(test) == [0], name
        else:
            assert reasons == [None] * len(reasons), name


def test_suite_covers_spec_scenarios():
    tests = {name.split(".", 1)[1]: test for name, test in _collect([LOCAL, UNSUPPORTED, KNOWN_ABSENT]).items()}
    assert set(tests) == {
        "local_only_inconclusive_with_created_at_after_condition",
        "local_only_inconclusive_with_created_at_before_condition",
        "local_only_inconclusive_without_person_properties",
        "local_only_inconclusive_with_created_at_known_absent",
        "default_getter_falls_back_with_created_at_after_condition",
        "default_getter_falls_back_with_created_at_before_condition",
        "default_getter_falls_back_without_person_properties",
        "negated_leaf_stays_inconclusive",
        "outer_not_in_stays_inconclusive",
        "other_leaf_decides_and_group",
        "created_at_person_filter_matches_caller_property",
        "default_getter_resolves_person_filter_locally",
        "unknown_type_defers_only_that_flag",
        "false_person_filter_decides_condition",
        "later_catch_all_condition_decides",
    }

    for name, test in tests.items():
        evaluations = _evaluations(test)
        assert all("force_remote" not in e for e in evaluations), name
        if name.startswith("default_getter_falls_back"):
            assert [e.get("allow_remote_fallback") for e in evaluations] == [True], name
            assert _flags_count(test) == [1], name
            assert _results(test) == [{"field": "value", "expected": "remote-value"}], name
        elif name == "default_getter_resolves_person_filter_locally":
            assert [e.get("allow_remote_fallback") for e in evaluations] == [True], name
            assert _flags_count(test) == [0], name
            assert _results(test) == [{"field": "value", "expected": True}], name
        else:
            assert all(e.get("only_evaluate_locally") is True for e in evaluations), name
            assert _flags_count(test) == [0], name
        # Every case except the person-type checks has an unsupported filter type.
        has_unsupported = bool(set(_filter_types(test)) - SUPPORTED_TYPES)
        person_only = {
            "created_at_person_filter_matches_caller_property",
            "default_getter_resolves_person_filter_locally",
        }
        assert has_unsupported is (name not in person_only), name

    created_at_values = [
        (e.get("person_properties") or {}).get("created_at")
        for name, test in tests.items()
        if name.startswith("local_only_inconclusive_")
        for e in _evaluations(test)
    ]
    assert created_at_values == ["2026-06-01", "2020-06-01", None, None]
    for name in ("negated_leaf_stays_inconclusive", "outer_not_in_stays_inconclusive"):
        assert [e.get("expect_inconclusive") for e in _evaluations(tests[name])] == [True], name

    person_types = tests["created_at_person_filter_matches_caller_property"]
    flags = person_types["steps"][0]["params"]["definitions"]["flags"]
    type_states = [f["filters"]["groups"][0]["properties"][0] for f in flags]
    assert [("type" in p, p.get("type")) for p in type_states] == [
        (True, "person"),
        (False, None),
        (True, None),
        (True, ""),
    ]
    assert all(p["key"] == "created_at" for p in type_states)
    results = [s["params"] for s in person_types["steps"] if s["action"] == "assert_action_result"]
    assert results == [{"field": "value", "expected": True}] * 4

    defers = _evaluations(tests["unknown_type_defers_only_that_flag"])
    assert [(e["key"], e.get("expect_inconclusive")) for e in defers] == [("future-type", True), ("beta-ui", None)]


def test_expect_inconclusive_is_used_only_by_this_suite():
    # The local evaluation v2 suite passes it through get_feature_flag_for_local_case.
    for name, definition in ContractExecutor().get_test_suites().items():
        if name in {SUITE, "feature_flags_local_evaluation_v2"}:
            continue
        for category in definition.get("categories", {}).values():
            for test in category.get("tests", []):
                for step in test.get("steps", []):
                    params = step.get("params", {})
                    assert "expect_inconclusive" not in params, (name, test["name"])
                    assert "expected_unresolved_reason" not in params, (name, test["name"])


def _ctx(response, state=None):
    adapter = SimpleNamespace(get_feature_flag=AsyncMock(return_value=response))
    return SimpleNamespace(sdk_adapter=adapter, mock_server=state or MockServerState())


INCONCLUSIVE = {"key": "k", "distinct_id": "u", "only_evaluate_locally": True, "expect_inconclusive": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"success": False},
        {"success": False, "value": None, "locally_evaluated": True},
        {"success": False, "value": None, "error": None},
    ],
)
async def test_expect_inconclusive_accepts_no_value(response):
    ctx = _ctx(response)
    assert await GetFeatureFlagAction().execute(dict(INCONCLUSIVE), ctx) == response
    request = ctx.sdk_adapter.get_feature_flag.await_args.args[0]
    payload = SDKAdapterClient("http://adapter")._feature_flag_payload(request)
    assert payload == {"key": "k", "distinct_id": "u", "only_evaluate_locally": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"success": True, "value": False, "locally_evaluated": True},
        {"success": True, "value": True, "locally_evaluated": True},
        {"success": True, "value": "variant"},
        None,
        {},
        {"error": "evaluation failed"},
        {"success": False, "value": None, "error": "evaluation failed"},
        {"success": True, "value": None, "locally_evaluated": False},
        {"success": True, "value": None},
    ],
)
async def test_expect_inconclusive_rejects_a_value(response):
    with pytest.raises(AssertionError, match="Local-only evaluation"):
        await GetFeatureFlagAction().execute(dict(INCONCLUSIVE), _ctx(response))


@pytest.mark.asyncio
async def test_expect_inconclusive_still_rejects_flags_requests():
    state = MockServerState()
    state.record_request("POST", "/flags/", {}, {}, b"{}")
    with pytest.raises(AssertionError, match="remote /flags"):
        await GetFeatureFlagAction().execute(dict(INCONCLUSIVE), _ctx({"success": False, "value": None}, state))


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"allow_remote_fallback": True}, {"only_evaluate_locally": False}])
async def test_expect_inconclusive_requires_local_only(params):
    with pytest.raises(ValueError, match="expect_inconclusive"):
        await GetFeatureFlagAction().execute(
            {"key": "k", "distinct_id": "u", "expect_inconclusive": True, **params}, _ctx({"success": True})
        )


@pytest.mark.asyncio
async def test_expected_unresolved_reason_accepts_the_reported_reason():
    response = {"success": False, "value": None, "unresolved_reason": "unsupported_definition"}
    params = {**INCONCLUSIVE, "expected_unresolved_reason": "unsupported_definition"}
    ctx = _ctx(response)
    assert await GetFeatureFlagAction().execute(params, ctx) == response
    request = ctx.sdk_adapter.get_feature_flag.await_args.args[0]
    payload = SDKAdapterClient("http://adapter")._feature_flag_payload(request)
    assert payload == {"key": "k", "distinct_id": "u", "only_evaluate_locally": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "missing_context"])
async def test_expected_unresolved_reason_rejects_another_reason(reason):
    response = {"success": False, "value": None}
    if reason is not None:
        response["unresolved_reason"] = reason
    params = {**INCONCLUSIVE, "expected_unresolved_reason": "unsupported_definition"}
    with pytest.raises(AssertionError, match="unresolved_reason"):
        await GetFeatureFlagAction().execute(params, _ctx(response))


@pytest.mark.asyncio
async def test_expected_unresolved_reason_requires_expect_inconclusive():
    params = {"key": "k", "distinct_id": "u", "only_evaluate_locally": True}
    with pytest.raises(ValueError, match="expected_unresolved_reason"):
        await GetFeatureFlagAction().execute(
            {**params, "expected_unresolved_reason": "unsupported_definition"}, _ctx({"success": True})
        )
