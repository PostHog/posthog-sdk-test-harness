"""The local evaluation suite for config version 2 flags, and the contract helpers behind it."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from posthog_test_harness import flags_v4
from posthog_test_harness import local_evaluation_v2 as lev2
from posthog_test_harness.actions import (
    AssertDefinitionsRequestQueryAction,
    AssertFeedSiblingsEvaluateLocallyAction,
    AssertLastDefinitionsRequestAction,
    AssertLocalCaseEventsAction,
    AssertLocalCasePayloadAction,
    AssertLocalCaseValueAction,
    ConfigureLocalEvaluationV2DefinitionsAction,
    ConfigureLocalEvaluationV2FallbackResponseAction,
    GetFeatureFlagForLocalCaseAction,
    ReloadFeatureFlagDefinitionsAction,
    get_all_actions,
)
from posthog_test_harness.contract import ContractExecutor
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState
from posthog_test_harness.tests.suites import ContractTestSuite

SUITE = "feature_flags_local_evaluation_v2"
V1 = "feature_flags_local_evaluation_v1"
V2 = "feature_flags_local_evaluation_v2"
PAYLOADS = "feature_flag_payloads"
REASONS = "feature_flags_unresolved_reasons_v1"
PATH = "/flags/definitions"


def _names(capabilities, sdk_type="server") -> set:
    return {name for name, _ in ContractTestSuite(SUITE, ContractExecutor()).collect_tests(sdk_type, capabilities)}


def _schema(name):
    # Resolve the contract's cross-schema references offline, from the package itself.
    schemas = [
        flags_v4._load(str(path.relative_to(flags_v4.contract_root())))
        for path in sorted((flags_v4.contract_root() / "schemas").glob("*.json"))
    ]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return Draft202012Validator(flags_v4._load(f"schemas/{name}"), registry=registry, format_checker=FormatChecker())


# --- Selection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "capabilities", [None, [], [V1], [V2], [V1, PAYLOADS, REASONS], ["feature_flags_local_evaluation"]]
)
def test_the_suite_needs_both_local_evaluation_capabilities(capabilities):
    assert _names(capabilities) == set()


def test_existing_suites_are_unchanged_by_the_new_capability():
    for suite in set(ContractExecutor().get_test_suites()) - {SUITE}:
        tests = ContractTestSuite(suite, ContractExecutor())
        before = {name for name, _ in tests.collect_tests("server", [V1, PAYLOADS, REASONS, "flags_response_v4"])}
        after = {name for name, _ in tests.collect_tests("server", [V1, V2, PAYLOADS, REASONS, "flags_response_v4"])}
        assert before == after, suite


def test_payload_and_reason_tests_need_their_capabilities():
    base = _names([V1, V2])
    with_payloads = _names([V1, V2, PAYLOADS]) - base
    with_reasons = _names([V1, V2, REASONS]) - base
    assert with_payloads == {f"feed.v2_entry_payload[{k}]" for k in lev2.case_ids("feed_v2_payloads")} | {
        f"corpus.conclusive_case_payload[{c}]" for c in lev2.case_ids("corpus_payloads")
    }
    assert with_reasons == {
        f"feed.unsupported_entry_reports_unsupported_definition[{k}]" for k in lev2.case_ids("feed_unsupported")
    } | {f"corpus.inconclusive_case_reports_its_reason[{c}]" for c in lev2.case_ids("corpus_inconclusive")}


def test_every_step_is_a_known_action_and_every_case_resolves():
    actions = get_all_actions()
    for name, test in ContractTestSuite(SUITE, ContractExecutor()).collect_tests("server", [V1, V2, PAYLOADS, REASONS]):
        for step in test["steps"]:
            assert step["action"] in actions, (name, step["action"])
            params = step.get("params", {})
            assert "{case}" not in json.dumps(params), name
            if "source" in params:
                assert params["source"] in lev2.SOURCES
                case = params.get("case")
                if case is not None:
                    assert case in lev2.case_ids(params["source"]) or params["source"].startswith("feed"), name


# --- Sources and expectations ------------------------------------------------


def test_feed_sources_partition_the_feed():
    entries = [entry["key"] for entry in lev2._feed()["feed"]["flags"]]
    v1, v2, unsupported = (lev2.case_ids(s) for s in ("feed_v1", "feed_v2", "feed_unsupported"))
    assert sorted(v1 + v2 + unsupported) == sorted(entries)
    assert set(lev2.case_ids("feed_v2_payloads")) <= set(v2)
    # The two entries contract 10.2.0 added are read anyway: inactive wins, the unknown entry field is ignored.
    assert {"retired-future-rule-type", "future-entry-field"} <= set(v2)
    assert len(unsupported) == 3


def test_every_mapped_case_is_run_or_has_a_reason():
    reasons = {}
    for case_id, _ in lev2._corpus_cases():
        reasons[case_id] = lev2.black_box_reason(case_id)
    run = {c for c, r in reasons.items() if r is None}
    assert run == set(lev2.case_ids("corpus_conclusive")) | set(lev2.case_ids("corpus_inconclusive"))
    assert set(reasons.values()) - {None} <= {
        "white_box",
        "not_applicable",
        "timezone",
        "identifier",
        "relative_date",
        "complete_map",
    }
    # Every local corpus case runs, and every parse error does.
    for case_id, corpus in lev2._corpus_cases():
        if (
            corpus.endswith("v2_local_evaluation.json")
            or lev2._corpus_case(case_id)["expected"]["status"] == "parse_error"
        ):
            assert reasons[case_id] is None, case_id


@pytest.mark.parametrize(
    "case_id,reason",
    [
        ("v2_boolean.threshold.p0_equal", "white_box"),
        ("v2_boolean.context.unavailable", None),
        ("v2_boolean.context.unneeded", None),
    ],
)
def test_black_box_reasons(case_id, reason):
    assert lev2.black_box_reason(case_id) == reason


def test_a_complete_map_missing_a_read_key_is_left_out():
    for case_id, _ in lev2._corpus_cases():
        case = lev2._corpus_case(case_id)
        context = case["context"]
        keys = {p.get("key") for p in lev2._predicates(case["config"])}
        if (
            lev2.black_box_reason(case_id) is None
            and context.get("properties_complete")
            and isinstance(context.get("properties"), dict)
        ):
            assert keys <= set(context["properties"]), case_id


@pytest.mark.parametrize("source", ["feed_v2", "corpus_conclusive"])
def test_event_projections_satisfy_the_contract_schemas(source):
    called, exposure = _schema("feature_flag_called_context.schema.json"), _schema(
        "experiment_exposure_properties.schema.json"
    )
    for case in lev2.case_ids(source):
        expected = lev2.expectation(source, case)
        assert not list(called.iter_errors(expected["called"])), case
        assert lev2.called_projection(expected["called"]) == expected["called"]
        if expected["exposure"] is not None:
            assert expected["called"]["$feature_flag_reason_code"] == "experiment_split"
            assert not list(exposure.iter_errors(expected["exposure"])), case
        else:
            assert expected["called"]["$feature_flag_reason_code"] != "experiment_split"


def test_feed_expectations_come_from_the_fixture():
    assert lev2.expectation("feed_v2", "results-per-page") == {
        "key": "results-per-page",
        "local_evaluation": "conclusive",
        "legacy_value": True,
        "legacy_payload": 20,
        "called": {
            "$feature_flag": "results-per-page",
            "$feature_flag_response": True,
            "$feature_flag_config_version": 2,
            "$feature_flag_reason_code": "experiment_split",
            "$feature_flag_rule_id": "11111111-1111-4111-8111-111111111111",
            "$feature_flag_rule_type": "experiment",
            "$feature_flag_variant_key": "control",
        },
        "exposure": {
            "$feature_flag": "results-per-page",
            "$feature_flag_response": True,
            "$feature_flag_variant_key": "control",
            "$feature_flag_rule_id": "11111111-1111-4111-8111-111111111111",
            "locally_evaluated": True,
        },
        "flag_id": 308,
        "flag_version": 5,
    }
    disabled = lev2.expectation("feed_v2", "retired-future-rule-type")
    assert disabled["legacy_value"] is False
    assert disabled["called"] == {
        "$feature_flag": "retired-future-rule-type",
        "$feature_flag_response": False,
        "$feature_flag_config_version": 2,
        "$feature_flag_reason_code": "flag_disabled",
    }
    assert lev2.expectation("feed_unsupported", "future-rule-type") == {
        "key": "future-rule-type",
        "local_evaluation": "inconclusive",
        "unresolved_reason": "unsupported_definition",
    }
    assert lev2.expectation("feed_v1", "legacy-banner") == {"key": "legacy-banner", "local_evaluation": "conclusive"}


def test_corpus_unresolved_reasons_follow_the_local_outcomes():
    reasons = {
        lev2.expectation("corpus_inconclusive", c)["unresolved_reason"] for c in lev2.case_ids("corpus_inconclusive")
    }
    assert reasons == {"missing_context", "unsupported_definition"}
    assert lev2.expectation("corpus_inconclusive", "v2_local.context.miss_before_missing")["unresolved_reason"] == (
        "missing_context"
    )


def test_corpus_documents_satisfy_the_producer_schema_and_select_the_matching_version():
    schema = _schema("definitions_v2.schema.json")
    for case in lev2.case_ids("corpus_conclusive"):
        legacy, v2 = lev2.documents("corpus_conclusive", case)
        assert legacy["flags"] == []
        assert not list(schema.iter_errors(v2)), case
        explicit = lev2._corpus_case(case)["context"].get("explicit_exact_matching", True)
        assert v2["property_matching_version"] == (2 if explicit else 1)
        assert v2["flags"][0]["active"] is lev2._corpus_case(case)["flag"].get("active", True)


def test_the_feed_legacy_document_has_only_its_config_version_1_entries():
    legacy, v2 = lev2.documents("feed_unsupported", "future-rule-type")
    assert [e["key"] for e in legacy["flags"]] == lev2.case_ids("feed_v1")
    assert v2 == lev2._feed()["feed"]
    assert {k: v for k, v in legacy.items() if k != "flags"} == {k: v for k, v in v2.items() if k != "flags"}


@pytest.mark.parametrize("source", ["feed_unsupported", "corpus_inconclusive"])
def test_fallback_responses_satisfy_the_producer_schema_and_differ_from_a_lenient_answer(source):
    schema = _schema("flags_response_v4.schema.json")
    for case in lev2.case_ids(source):
        body = lev2.fallback_response(source, case)
        assert not list(schema.iter_errors(body)), case
        [record] = body["flags"].values()
        config = lev2._expected_and_identity(source, case)[1]["filters"]
        default = config.get("default_value") if isinstance(config, dict) else None
        if isinstance(default, (bool, str)):
            assert record["value"] != default, case
        values = (
            [r.get("value") for r in config.get("rules", []) if isinstance(r, dict)] if isinstance(config, dict) else []
        )
        if isinstance(record["value"], (bool, str)) and record["value"] is not True:
            assert record["value"] not in values, case


def test_a_conclusive_case_has_no_fallback():
    with pytest.raises(ValueError, match="conclusive"):
        lev2.fallback_response("feed_v2", "layout-preview")


def test_reads_take_the_case_context():
    assert lev2.read("feed_v2", "checkout-copy") == {
        "key": "checkout-copy",
        "distinct_id": "person-example",
        "person_properties": {"plan": "pro"},
    }
    assert lev2.read("feed_unsupported", "future-rule-type", entry="layout-preview")["key"] == "layout-preview"
    assert lev2.read("corpus_conclusive", "v2_boolean.context.unneeded")["person_properties"] is None
    with pytest.raises(ValueError):
        lev2.read("feed_v2", "no-such-entry")
    with pytest.raises(ValueError):
        lev2.case_ids("readers")


# --- Actions -----------------------------------------------------------------


def _ctx(state=None, **adapter):
    return SimpleNamespace(
        mock_server=state or MockServerState(),
        sdk_adapter=SimpleNamespace(**{k: AsyncMock(**v) for k, v in adapter.items()}),
        last_action_result=None,
    )


def _get(client, query=(), headers=None):
    return client.get(
        PATH,
        query_string=[("token", "phc_test_key"), *query],
        headers={"Authorization": "Bearer phx_test_key", **(headers or {})},
    )


async def test_configure_serves_the_case_documents_by_v():
    ctx = _ctx()
    await ConfigureLocalEvaluationV2DefinitionsAction().execute(
        {"source": "corpus_conclusive", "case": "v2_local.flag_disabled.number", "etag": '"e"'}, ctx
    )
    client = MockServer(ctx.mock_server).app.test_client()
    assert _get(client).get_json()["flags"] == []
    v2 = _get(client, [("v", "2")])
    assert v2.headers["ETag"] == '"e"'
    assert v2.get_json()["flags"][0]["key"] == "example-display-key"
    ctx.mock_server.reset()
    await ConfigureLocalEvaluationV2DefinitionsAction().execute({"source": "feed_v1", "serve_v2": False}, ctx)
    assert [f["key"] for f in _get(client, [("v", "2")]).get_json()["flags"]] == lev2.case_ids("feed_v1")


@pytest.mark.parametrize(
    "params,sent",
    [
        ({"mode": "local_only"}, {"only_evaluate_locally": True}),
        ({"mode": "default"}, {"allow_remote_fallback": True}),
    ],
)
async def test_case_reads_send_the_case_context_and_mode(params, sent):
    ctx = _ctx(get_feature_flag={"return_value": {"success": True, "value": "short", "locally_evaluated": True}})
    await GetFeatureFlagForLocalCaseAction().execute({"source": "feed_v2", "case": "checkout-copy", **params}, ctx)
    request = ctx.sdk_adapter.get_feature_flag.await_args.args[0]
    assert (request.key, request.distinct_id, request.person_properties) == (
        "checkout-copy",
        "person-example",
        {"plan": "pro"},
    )
    for field, value in sent.items():
        assert getattr(request, field) is value


async def test_an_inconclusive_case_read_checks_the_unresolved_reason():
    ctx = _ctx(
        get_feature_flag={
            "return_value": {
                "success": False,
                "value": None,
                "locally_evaluated": False,
                "unresolved_reason": "missing_context",
            }
        }
    )
    params = {"source": "feed_unsupported", "case": "future-rule-type", "expect_inconclusive": True}
    await GetFeatureFlagForLocalCaseAction().execute(params, ctx)
    with pytest.raises(AssertionError, match="unsupported_definition"):
        await GetFeatureFlagForLocalCaseAction().execute({**params, "check_unresolved_reason": True}, ctx)
    with pytest.raises(ValueError):
        await GetFeatureFlagForLocalCaseAction().execute({**params, "mode": "default"}, ctx)


@pytest.mark.parametrize("value,passes", [(True, True), (1, False), ("true", False), (None, False)])
async def test_value_assertion_is_type_strict(value, passes):
    ctx = _ctx()
    ctx.last_action_result = {"success": True, "value": value}
    action = AssertLocalCaseValueAction().execute({"source": "feed_v2", "case": "search-ranking"}, ctx)
    if passes:
        await action
    else:
        with pytest.raises(AssertionError):
            await action


async def test_value_assertion_reads_the_fallback_and_refuses_an_unstated_value():
    ctx = _ctx()
    ctx.last_action_result = {"success": True, "value": "remote-fallback"}
    await AssertLocalCaseValueAction().execute(
        {"source": "feed_unsupported", "case": "future-rule-field", "fallback": True}, ctx
    )
    with pytest.raises(ValueError, match="no value"):
        await AssertLocalCaseValueAction().execute({"source": "feed_v1", "case": "legacy-banner"}, ctx)
    ctx.last_action_result = {"success": False, "value": True}
    with pytest.raises(AssertionError, match="success"):
        await AssertLocalCaseValueAction().execute({"source": "feed_v2", "case": "layout-preview"}, ctx)


async def test_payload_assertion_requires_no_flags_request():
    state = MockServerState()
    ctx = _ctx(state)
    ctx.last_action_result = {"success": True, "value": {"boost": 1.5, "model": "semantic"}}
    await AssertLocalCasePayloadAction().execute({"source": "feed_v2_payloads", "case": "search-ranking"}, ctx)
    state.record_request("POST", "/flags/", {}, {"v": "4"}, b"{}")
    with pytest.raises(AssertionError, match="/flags"):
        await AssertLocalCasePayloadAction().execute({"source": "feed_v2_payloads", "case": "search-ranking"}, ctx)


async def test_fallback_response_is_queued_for_flags_only():
    ctx = _ctx()
    await ConfigureLocalEvaluationV2FallbackResponseAction().execute(
        {"source": "corpus_inconclusive", "case": "v2_local.context.miss_before_missing"}, ctx
    )
    client = MockServer(ctx.mock_server).app.test_client()
    assert client.post("/batch", json={"batch": []}).status_code == 200
    body = client.post("/flags/?v=4", json={}).get_json()
    assert body == lev2.fallback_response("corpus_inconclusive", "v2_local.context.miss_before_missing")


def _events(state, *events):
    state.record_request("POST", "/batch", {}, {}, json.dumps({"api_key": "k", "batch": list(events)}).encode())


def _called(expected, **changes):
    properties = {
        **expected["called"],
        "locally_evaluated": True,
        "$feature_flag_id": expected["flag_id"],
        "$feature_flag_version": expected["flag_version"],
        "$lib": "test",
    }
    properties.update(changes)
    return {"event": "$feature_flag_called", "properties": {k: v for k, v in properties.items() if v is not None}}


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"locally_evaluated": False},
        {"$feature_flag_rule_id": "22222222-2222-4222-8222-222222222222"},
        {"$feature_flag_reason": "Matched condition set 1"},
        {"$feature_flag_reason_code": None},
        {"$feature_flag_version": 6},
    ],
)
async def test_event_assertion_checks_the_whole_called_context(change):
    state = MockServerState()
    expected = lev2.expectation("feed_v2", "results-per-page")
    _events(
        state,
        _called(expected, **change),
        {"event": "$experiment_exposure", "properties": {**expected["exposure"], "$lib": "test"}},
    )
    action = AssertLocalCaseEventsAction().execute({"source": "feed_v2", "case": "results-per-page"}, _ctx(state))
    if not change:
        await action
    else:
        with pytest.raises(AssertionError):
            await action


@pytest.mark.parametrize("exposures", [0, 2])
async def test_event_assertion_counts_exposures(exposures):
    state = MockServerState()
    expected = lev2.expectation("feed_v2", "results-per-page")
    _events(
        state, _called(expected), *[{"event": "$experiment_exposure", "properties": expected["exposure"]}] * exposures
    )
    with pytest.raises(AssertionError, match="experiment_exposure"):
        await AssertLocalCaseEventsAction().execute({"source": "feed_v2", "case": "results-per-page"}, _ctx(state))


async def test_event_assertion_refuses_an_exposure_without_a_split():
    state = MockServerState()
    expected = lev2.expectation("feed_v2", "layout-preview")
    _events(
        state, _called(expected), {"event": "$experiment_exposure", "properties": {"$feature_flag": "layout-preview"}}
    )
    with pytest.raises(AssertionError, match="no \\$experiment_exposure"):
        await AssertLocalCaseEventsAction().execute({"source": "feed_v2", "case": "layout-preview"}, _ctx(state))


@pytest.mark.parametrize(
    "queries,passes",
    [
        ([[("v", "2")]], True),
        ([[("v", "2")], [("v", "2")]], True),
        ([], False),
        ([[("v", "2")], []], False),
        ([[("v", "1")]], False),
        ([[("v", "2"), ("v", "2")]], False),
    ],
)
async def test_definitions_query_assertion_reads_every_request(queries, passes):
    state = MockServerState()
    client = MockServer(state).app.test_client()
    for query in queries:
        _get(client, query)
    action = AssertDefinitionsRequestQueryAction().execute({"param": "v", "expected": "2"}, _ctx(state))
    if passes:
        await action
    else:
        with pytest.raises(AssertionError):
            await action


async def test_last_definitions_request_assertion():
    state = MockServerState()
    state.set_v2_definitions({"flags": []}, etag='"e"')
    client = MockServer(state).app.test_client()
    _get(client, [("v", "2")])
    _get(client, [("v", "2")], {"If-None-Match": '"e"'})
    await AssertLastDefinitionsRequestAction().execute({"status": 304, "if_none_match": '"e"'}, _ctx(state))
    with pytest.raises(AssertionError, match="status"):
        await AssertLastDefinitionsRequestAction().execute({"status": 200}, _ctx(state))
    with pytest.raises(AssertionError, match="If-None-Match"):
        await AssertLastDefinitionsRequestAction().execute({"if_none_match": '"other"'}, _ctx(state))


@pytest.mark.parametrize("statuses,passes", [([304], True), ([200], False), ([304, 200], False), ([], False)])
async def test_a_not_modified_reload_needs_only_304_answers(statuses, passes):
    state = MockServerState()

    async def reload(timeout_ms):
        for status in statuses:
            request = state.record_definition_request(PATH, {}, {"token": "phc_test_key"})
            request.response_status = status
        return {"success": True, "ready": True}

    ctx = _ctx(state)
    ctx.sdk_adapter.reload_feature_flag_definitions = AsyncMock(side_effect=reload)
    action = ReloadFeatureFlagDefinitionsAction().execute({"expect_not_modified": True}, ctx)
    if passes:
        await action
    else:
        with pytest.raises(AssertionError, match="304"):
            await action


async def test_siblings_assertion_allows_an_earlier_fallback_but_not_a_new_one():
    state = MockServerState()
    state.record_request("POST", "/flags/", {}, {"v": "4"}, b"{}")

    async def local(request):
        expected = lev2.expectation("feed_v2" if request.key in lev2.case_ids("feed_v2") else "feed_v1", request.key)
        return {"success": True, "value": expected.get("legacy_value", True), "locally_evaluated": True}

    ctx = _ctx(state)
    ctx.sdk_adapter.get_feature_flag = AsyncMock(side_effect=local)
    params = {"source": "feed_unsupported", "case": "future-rule-type"}
    await AssertFeedSiblingsEvaluateLocallyAction().execute(params, ctx)
    keys = [call.args[0].key for call in ctx.sdk_adapter.get_feature_flag.await_args_list]
    assert keys == lev2.case_ids("feed_v1") + lev2.case_ids("feed_v2")

    async def escapes(request):
        state.record_request("POST", "/flags/", {}, {"v": "4"}, b"{}")
        return await local(request)

    ctx.sdk_adapter.get_feature_flag = AsyncMock(side_effect=escapes)
    with pytest.raises(AssertionError, match="/flags"):
        await AssertFeedSiblingsEvaluateLocallyAction().execute(params, ctx)

    async def wrong(request):
        return {"success": True, "value": "wrong", "locally_evaluated": True}

    ctx.sdk_adapter.get_feature_flag = AsyncMock(side_effect=wrong)
    with pytest.raises(AssertionError, match="legacy value"):
        await AssertFeedSiblingsEvaluateLocallyAction().execute(params, ctx)
