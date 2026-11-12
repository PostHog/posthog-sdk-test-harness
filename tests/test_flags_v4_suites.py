"""The remote /flags?v=4 read suite, and the fixture helpers behind it."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from posthog_test_harness import flags_v4
from posthog_test_harness.actions import (
    AssertLegacyFlagPayloadForCaseAction,
    AssertLegacyFlagValueForCaseAction,
    ConfigureFlagsV4ResponseAction,
    GetFeatureFlagPayloadAction,
)
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.tests.suites import ContractTestSuite
from posthog_test_harness.types import FeatureFlagRequest, MockResponse

SUITE = "feature_flags_v4"
ALL_CAPABILITIES = ["capture_v0", "flags_response_v4", "feature_flag_payloads"]
READER_IDS = flags_v4.case_ids("readers")
VALUE_IDS = flags_v4.case_ids("values")
PAYLOAD_READER_IDS = flags_v4.case_ids("reader_payloads")


def _names(suite: str, capabilities) -> set:
    return {name for name, _ in ContractTestSuite(suite, ContractExecutor()).collect_tests("server", capabilities)}


def _expanded(test: str, case_ids) -> set:
    return {f"{test}[{case_id}]" for case_id in case_ids}


# --- Selection ---------------------------------------------------------------


@pytest.mark.parametrize("capabilities", [None, ["capture_v0"], ["capture_v0", "feature_flag_payloads"]])
def test_suite_selects_nothing_without_flags_response_v4(capabilities) -> None:
    assert _names(SUITE, capabilities) == set()


@pytest.mark.parametrize("sdk_type", ["client", "web"])
def test_suite_is_server_only(sdk_type) -> None:
    suite = ContractTestSuite(SUITE, ContractExecutor())
    assert suite.collect_tests(sdk_type, ALL_CAPABILITIES) == []


def test_existing_suites_are_unchanged_by_the_new_capability() -> None:
    suites = set(ContractExecutor().get_test_suites()) - {SUITE}
    for suite in suites:
        before = _names(suite, ["capture_v0", "capture_v1", "flags_response_v4"])
        after = _names(suite, ["capture_v0", "capture_v1", "flags_response_v4", "feature_flag_payloads"])
        assert before == after, suite


def test_flags_response_v4_alone_selects_only_the_legacy_getter_tests() -> None:
    assert _names(SUITE, ["flags_response_v4"]) == _expanded(
        "legacy_getters.legacy_getter_reads_reader_fixture", READER_IDS
    ) | _expanded("legacy_getters.legacy_getter_reads_typed_value", VALUE_IDS)


def test_feature_flag_payloads_adds_one_payload_test_per_case() -> None:
    added = _names(SUITE, ALL_CAPABILITIES) - _names(SUITE, ["flags_response_v4"])
    assert added == _expanded("legacy_payloads.legacy_payload_reads_reader_fixture", PAYLOAD_READER_IDS) | _expanded(
        "legacy_payloads.legacy_payload_is_the_typed_value", VALUE_IDS
    )


def test_expanded_tests_substitute_the_case_id() -> None:
    suite = ContractTestSuite(SUITE, ContractExecutor())
    [(_, test)] = [
        (name, test)
        for name, test in suite.collect_tests("server", ["flags_response_v4"])
        if name == "legacy_getters.legacy_getter_reads_reader_fixture[readers.holdout]"
    ]
    assert "cases" not in test
    assert "{case}" not in json.dumps(test)
    assert test["steps"][0]["params"] == {"source": "readers", "case": "readers.holdout"}


def test_reader_payloads_are_every_reader_case() -> None:
    # readers.v1_failed, a failed record that carries a payload, is decided by its legacy_payload expectation.
    assert PAYLOAD_READER_IDS == READER_IDS


def test_an_unknown_case_source_fails_collection() -> None:
    with pytest.raises(ValueError, match="Unknown flags v4 fixture source"):
        flags_v4.case_ids("nope")


def test_every_case_test_step_is_a_known_action() -> None:
    actions = set(ContractExecutor().actions)
    suite = ContractTestSuite(SUITE, ContractExecutor())
    for name, test in suite.collect_tests("server", ALL_CAPABILITIES):
        assert {step["action"] for step in test["steps"]} <= actions, name


# --- Fixtures and expectations ----------------------------------------------


def test_value_case_responses_satisfy_the_producer_schema() -> None:
    schema = json.loads((flags_v4.contract_root() / "schemas/flags_response_v4.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for case_id in VALUE_IDS:
        assert not list(validator.iter_errors(flags_v4.response("values", case_id))), case_id


def test_reader_responses_are_the_fixture_documents() -> None:
    body = flags_v4.response("readers", "readers.partial_split_missing_variant_key")
    assert "variant_key" not in body["flags"]["garden-layout"]["metadata"]
    body = flags_v4.response("readers", "readers.integral_json_number_context")
    assert json.dumps(body["flags"]["garden-layout"]["metadata"]["config_version"]) == "2.0"


@pytest.mark.parametrize(
    "case_id, legacy_value, legacy_payload",
    [
        ("readers.complete_split", False, None),
        ("readers.old_server_variant", "compact", None),
        ("readers.old_server_boolean", True, None),
        ("readers.old_server_null_variant", True, None),
        ("readers.old_server_false", False, None),
        ("readers.partial_split_string_value", "usable-copy", None),
        ("readers.v1_at_v4", "compact", {"columns": 2}),
        ("readers.missing_flag", None, None),
        ("readers.missing_group_key", False, None),
        ("readers.split_wrong_type_rule_id", False, None),
        ("readers.error_with_value", False, None),
        ("readers.v1_failed", False, None),
    ],
)
def test_reader_expectations(case_id, legacy_value, legacy_payload) -> None:
    expected = flags_v4.expectation("readers", case_id)
    assert (expected["legacy_value"], expected["legacy_payload"]) == (legacy_value, legacy_payload)


def test_value_expectations_follow_the_corpus_legacy_cell() -> None:
    number = flags_v4.expectation("values", "v2_value.number.zero")
    assert (number["legacy_value"], number["legacy_payload"]) == (True, 0)
    null = flags_v4.expectation("values", "v2_value.object.no_match_null")
    assert (null["legacy_value"], null["legacy_payload"]) == (False, None)
    string = flags_v4.expectation("values", "v2_value.string.target")
    assert string["legacy_payload"] is None and isinstance(string["legacy_value"], str)


def test_value_records_carry_a_null_payload() -> None:
    for case_id in VALUE_IDS:
        [record] = flags_v4.response("values", case_id)["flags"].values()
        assert record["metadata"]["payload"] is None, case_id


@pytest.mark.parametrize(
    "actual, expected, equal",
    [
        (True, True, True),
        (1, True, False),
        (0, False, False),
        (2, 2.0, True),
        (json.loads("1e-07"), json.loads("1e-7"), True),
        ({"a": 1, "b": [1, None]}, {"b": [1, None], "a": 1}, True),
        ({"a": 1}, {"a": 1, "b": 2}, False),
        ("1", 1, False),
        (None, False, False),
    ],
)
def test_json_equal(actual, expected, equal) -> None:
    assert flags_v4.json_equal(actual, expected) is equal


# --- Mock responses ----------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_v4_response_is_served_byte_for_byte() -> None:
    state = MockServerState()
    await ConfigureFlagsV4ResponseAction().execute(
        {"source": "readers", "case": "readers.integral_json_number_context", "repeat": 2},
        SimpleNamespace(mock_server=state),
    )
    client = MockServer(state).app.test_client()
    expected = json.dumps(flags_v4.response("readers", "readers.integral_json_number_context"))

    for _ in range(2):
        response = client.post("/flags/?v=4", json={"distinct_id": "someone"})
        assert response.status_code == 200
        assert response.get_data(as_text=True) == expected
        assert "featureFlags" not in response.get_json()
    # The queue is used up; later requests get the default response.
    assert "featureFlags" in client.post("/flags/?v=4", json={}).get_json()


@pytest.mark.asyncio
async def test_configured_v4_response_serves_only_flags_requests() -> None:
    state = MockServerState()
    await ConfigureFlagsV4ResponseAction().execute(
        {"source": "readers", "case": "readers.missing_flag"}, SimpleNamespace(mock_server=state)
    )
    client = MockServer(state).app.test_client()
    client.post("/batch/", json={"api_key": "k", "batch": []})
    expected = json.dumps(flags_v4.response("readers", "readers.missing_flag"))
    assert client.post("/flags", json={}).get_data(as_text=True) == expected


# --- Adapter calls -----------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_read_takes_the_key_from_the_case() -> None:
    adapter = SimpleNamespace(get_feature_flag_payload=AsyncMock(return_value={"success": True}))
    await GetFeatureFlagPayloadAction().execute(
        {"source": "readers", "case": "readers.flag_key_seed"}, SimpleNamespace(sdk_adapter=adapter)
    )
    adapter.get_feature_flag_payload.assert_awaited_once_with(
        FeatureFlagRequest(key="seed", distinct_id="flags-v4-person", force_remote=True)
    )


# --- Assertions --------------------------------------------------------------


def _served_state(case: str, query: str = "v=4", source: str = "readers") -> MockServerState:
    """A mock server that served the case's fixture to one /flags request."""
    state = MockServerState()
    state.set_response_queue(
        [MockResponse(body=json.dumps(flags_v4.response(source, case)), path="/flags", raw_body=True)]
    )
    MockServer(state).app.test_client().post(f"/flags/?{query}", json={"distinct_id": "someone"})
    return state


def _result_ctx(result, params=None, state=None):
    params = params or {}
    if state is None:
        state = _served_state(params.get("case", "readers.missing_flag"), source=params.get("source", "readers"))
    return SimpleNamespace(last_action_result={"success": True, **result}, mock_server=state)


@pytest.mark.asyncio
async def test_legacy_value_assertion_is_type_strict() -> None:
    params = {"source": "readers", "case": "readers.old_server_boolean"}
    await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": True}, params))
    with pytest.raises(AssertionError, match="legacy value"):
        await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": 1}, params))


def test_failed_record_is_a_boolean_failed_true_on_the_requested_record() -> None:
    # The YAML suite and the generated Gherkin getter feature both use this predicate.
    failed = [case for case in flags_v4.case_ids("readers") if flags_v4.failed_record("readers", case)]
    assert failed == ["readers.dependency_error", "readers.error", "readers.error_with_value", "readers.v1_failed"]
    assert not any(flags_v4.failed_record("values", case) for case in flags_v4.case_ids("values"))
    # A missing flag has no record; an unreadable record has no failed marker.
    assert not flags_v4.failed_record("readers", "readers.missing_flag")
    assert not flags_v4.failed_record("readers", "readers.missing_group_key")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id",
    ["readers.dependency_error", "readers.error", "readers.error_with_value", "readers.v1_failed"],
)
@pytest.mark.parametrize("result", [{"value": False}, {"value": None}, {}])
async def test_failed_records_accept_legacy_no_result_sentinels(case_id, result) -> None:
    params = {"source": "readers", "case": case_id}
    await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx(result, params))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, 0, 1, "variant", {"value": False}, []])
async def test_failed_records_reject_values_other_than_no_result_sentinels(value) -> None:
    params = {"source": "readers", "case": "readers.error_with_value"}
    with pytest.raises(AssertionError, match="legacy value"):
        await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": value}, params))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, case_id",
    [
        ("readers", "readers.old_server_false"),
        ("readers", "readers.complete_split"),
        ("readers", "readers.missing_group_key"),
        ("readers", "readers.split_wrong_type_rule_id"),
        ("values", "v2_value.object.no_match_null"),
    ],
)
async def test_null_does_not_replace_healthy_false_or_other_reader_errors(source, case_id) -> None:
    params = {"source": source, "case": case_id}
    await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": False}, params))
    with pytest.raises(AssertionError, match="legacy value"):
        await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": None}, params))


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [None, False, "true", 1])
async def test_null_failure_sentinel_requires_an_explicit_boolean_marker(monkeypatch, marker) -> None:
    params = {"source": "readers", "case": "readers.error_with_value"}
    body = flags_v4.response(params["source"], params["case"])
    record = body["flags"]["garden-layout"]
    if marker is None:
        record.pop("failed")
    else:
        record["failed"] = marker
    monkeypatch.setattr(flags_v4, "response", lambda source, case_id: body)
    with pytest.raises(AssertionError, match="legacy value"):
        await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": None}, params))


@pytest.mark.asyncio
@pytest.mark.parametrize("global_errors", [False, None])
async def test_null_failure_sentinel_does_not_depend_on_global_errors(monkeypatch, global_errors) -> None:
    params = {"source": "readers", "case": "readers.error_with_value"}
    body = flags_v4.response(params["source"], params["case"])
    if global_errors is None:
        body.pop("errorsWhileComputingFlags")
    else:
        body["errorsWhileComputingFlags"] = global_errors
    monkeypatch.setattr(flags_v4, "response", lambda source, case_id: body)
    await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": None}, params))


@pytest.mark.asyncio
async def test_legacy_value_assertion_requires_no_value_for_a_missing_flag() -> None:
    params = {"source": "readers", "case": "readers.missing_flag"}
    await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": None}, params))
    with pytest.raises(AssertionError, match="legacy value"):
        await AssertLegacyFlagValueForCaseAction().execute(params, _result_ctx({"value": False}, params))


@pytest.mark.asyncio
async def test_payload_assertion_compares_json_values() -> None:
    params = {"source": "values", "case": "v2_value.number.exponent"}
    await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": json.loads("1.5e-7")}, params))
    with pytest.raises(AssertionError, match="payload"):
        await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": "1.5e-07"}, params))


@pytest.mark.asyncio
async def test_payload_assertion_refuses_an_undecided_reader_case(monkeypatch) -> None:
    readers = _without_legacy_payload("readers.v1_failed")
    monkeypatch.setattr(flags_v4, "_readers", lambda: readers)
    params = {"source": "readers", "case": "readers.v1_failed"}
    assert "readers.v1_failed" not in flags_v4.case_ids("reader_payloads")
    with pytest.raises(ValueError, match="does not decide"):
        await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": None}, params))


@pytest.mark.asyncio
async def test_payload_assertion_uses_the_declared_legacy_payload(monkeypatch) -> None:
    params = {"source": "readers", "case": "readers.v1_failed"}
    await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": None}, params))
    with pytest.raises(AssertionError, match="payload"):
        await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": {"columns": 2}}, params))
    readers = copy.deepcopy(flags_v4._readers())
    next(case for case in readers["cases"] if case["id"] == "readers.v1_failed")["reader"]["legacy_payload"] = [1]
    monkeypatch.setattr(flags_v4, "_readers", lambda: readers)
    assert flags_v4.expectation("readers", "readers.v1_failed")["legacy_payload"] == [1]


def _without_legacy_payload(case_id: str) -> dict:
    readers = copy.deepcopy(flags_v4._readers())
    del next(case for case in readers["cases"] if case["id"] == case_id)["reader"]["legacy_payload"]
    return readers


@pytest.mark.asyncio
async def test_payload_assertion_reads_a_config_version_1_payload() -> None:
    params = {"source": "readers", "case": "readers.v1_at_v4"}
    await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": {"columns": 2}}, params))
    with pytest.raises(AssertionError, match="payload"):
        await AssertLegacyFlagPayloadForCaseAction().execute(params, _result_ctx({"value": '{"columns":2}'}, params))


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [AssertLegacyFlagValueForCaseAction, AssertLegacyFlagPayloadForCaseAction])
@pytest.mark.parametrize("case_id", ["readers.missing_flag", "readers.error_with_value"])
async def test_case_assertions_require_a_successful_adapter_result(action, case_id) -> None:
    params = {"source": "readers", "case": case_id}
    ctx = SimpleNamespace(
        last_action_result={"success": False, "error": "request failed"},
        mock_server=_served_state(params["case"]),
    )
    with pytest.raises(AssertionError, match="did not report success"):
        await action().execute(params, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [AssertLegacyFlagValueForCaseAction, AssertLegacyFlagPayloadForCaseAction])
@pytest.mark.parametrize("case_id", ["readers.missing_flag", "readers.error_with_value"])
async def test_case_assertions_require_the_sdk_to_read_the_fixture(action, case_id) -> None:
    params = {"source": "readers", "case": case_id}
    with pytest.raises(AssertionError, match="no /flags request"):
        await action().execute(params, _result_ctx({"value": None}, params, MockServerState()))


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [AssertLegacyFlagValueForCaseAction, AssertLegacyFlagPayloadForCaseAction])
@pytest.mark.parametrize("case_id", ["readers.missing_flag", "readers.error_with_value"])
async def test_case_assertions_require_a_v4_request(action, case_id) -> None:
    params = {"source": "readers", "case": case_id}
    state = _served_state(params["case"], query="v=2")
    with pytest.raises(AssertionError, match="expected v=4"):
        await action().execute(params, _result_ctx({"value": None}, params, state))


@pytest.mark.asyncio
async def test_configured_v4_response_serves_an_accessor_that_reads_twice() -> None:
    state = MockServerState()
    await ConfigureFlagsV4ResponseAction().execute(
        {"source": "values", "case": "v2_value.number.zero"}, SimpleNamespace(mock_server=state)
    )
    client = MockServer(state).app.test_client()
    expected = json.dumps(flags_v4.response("values", "v2_value.number.zero"))
    for _ in range(2):
        assert client.post("/flags/?v=4", json={}).get_data(as_text=True) == expected
