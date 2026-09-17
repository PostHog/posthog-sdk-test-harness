"""Seventeen amended YAML origins, real remote HTTP, native client callback/cache contrasts."""

import json
from copy import deepcopy
from types import SimpleNamespace

import aiohttp
import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.analytics_outcome_steps import absent_header
from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import BASE_CATALOG_HASH, BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, selection
from posthog_test_harness.v2.remote_flag_steps import count, event_count, event_property, field, query
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import save_receipt
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS
from tests.test_v2_remote_flag_blockers import BLOCKERS, ORIGINS
from tests.v2_flush_host import serve
from tests.v2_remote_flags_host import RemoteFlagsHost

FEATURE = SUITE + "/remote-flags-v1.feature"
CALLBACK_FEATURE = "acceptance/public/on-feature-flags.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [c.id for c in CASES]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def amended_calls(origin):
    calls = []
    for item in origin["source_inputs_and_ordered_assertions"]:
        name, args = item["action"], deepcopy(item.get("params", {}))
        if name == "init":
            calls.append(["/setup", {"project_token": args.get("api_key", "phc_test_key"), "config": {}}])
        elif name == "get_feature_flag":
            assert args.pop("force_remote") is True
            calls.append(["/get_feature_flag", args])
        elif name in ("capture", "flush"):
            calls.append(["/" + name, args])
    return calls


def test_exact_origins_amended_inputs_and_ordered_assertions():
    resolutions = json.loads((SPECS / SUITE / "approved-amendments.json").read_text())["flag_semantics_v1"][
        "resolutions"
    ]
    assert len(CASES) == len(resolutions) == len(ORIGINS) == 17
    assert sum(len(r["argument_translations"]) for r in resolutions) == 16
    for case, historical, origin, resolution in zip(CASES, BLOCKERS, ORIGINS, resolutions):
        row = case.migration
        assert row["legacy_id"] == origin["id"] == historical["legacy_id"] == resolution["legacy_id"]
        assert row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert row["native_sdk_evidence"] == []
        assert row["argument_translations"] == resolution["argument_translations"]
        assert "@server" in case.tags
        assert len(case.steps) == len(origin["steps"]) + 3
        for step, source in zip(case.steps[3:], historical["source_inputs_and_ordered_assertions"]):
            name, params = source["action"], source.get("params", {})
            if name == "get_feature_flag":
                assert json_equal(json_arguments(step), {k: v for k, v in params.items() if k != "force_remote"})
            elif name in ("configure_mock_responses", "capture"):
                assert json_equal(json_arguments(step), params)
            elif name == "init":
                assert (
                    step.text
                    == f'the SDK is initialized with token "{params.get("api_key", "phc_test_key")}"'
                    + " and no additional configuration"
                )
            elif name == "flush":
                assert step.text == "pending captures are flushed"
            else:
                expected = {
                    "assert_flags_request_count": lambda: (
                        f'exactly {params["expected"]} requests containing /flags should have been received'
                    ),
                    "assert_flags_request_field": lambda: (
                        f'the first flags request field "{params["field"]}" should equal JSON '
                        + json.dumps(params["expected"])
                    ),
                    "assert_flags_request_query_param": lambda: (
                        f'the first flags request query parameter "{params["param"]}"'
                        + f' should equal "{params["expected"]}"'
                    ),
                    "assert_no_requests_to_paths": lambda: 'no capture request should use "/decide"',
                    "assert_header_absent": lambda: f'the first request header "{params["header"]}" should be absent',
                    "assert_action_result": lambda: "the public flag getter should return JSON "
                    + json.dumps(params["expected"]),
                    "assert_event_count_with_name": lambda: (
                        f'exactly {params["expected"]} received events should be named "{params["name"]}"'
                    ),
                    "assert_event_property_in_named_event": lambda: (
                        f'a received event named "{params["event_name"]}"'
                        + f' should have property "{params["property"]}" equal to JSON '
                        + json.dumps(params["expected"])
                    ),
                }[name]()
                assert step.text == expected


async def test_all_17_real_http_exact_native_inputs_and_response_parsing(contracts, tmp_path):
    async with serve(contracts, host_type=RemoteFlagsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    save_receipt(tmp_path, report, diagnostics)
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 17
    actual = []
    for data in host.inputs:
        call = deepcopy(data["invoke"])
        if call["route"] == "/setup":
            assert call["args"]["config"].pop("host").startswith("http://127.0.0.1:")
        contracts.validate(
            contracts.operations[call["route"]]["arguments_schema"].split("/")[-1], call["args"], "catalog"
        )
        actual.append([call["route"], call["args"]])
    assert json_equal(actual, [c for h in BLOCKERS for c in amended_calls(h)])
    (tmp_path / "inputs.json").write_text(json.dumps(actual, indent=2))
    (tmp_path / "parsed-responses.json").write_text(
        json.dumps({case.id: engine.parsed_responses for case, engine in zip(CASES, host.engines)}, indent=2)
    )
    assert len(host.closed) == 17
    assert host.engines[13].parsed_responses == [
        {"featureFlags": {"signup-aa-test": "variant-a"}, "featureFlagPayloads": {}, "errorsWhileComputingFlags": False}
    ]
    assert sum(len(d["wire_requests"]) for d in diagnostics["cases"]) == 20


@pytest.mark.parametrize(
    "index,defect,code",
    [
        (0, "wrong_identity", "flag_request_field"),
        (0, "device_at_root", "flag_request_field"),
        (1, "wrong_version", "flag_request_query"),
        (2, "decide", "flag_request_count"),
        (3, "authorization", "header_present"),
        (4, "wrong_token", "flag_request_field"),
        (4, "token_shadows_alias", "flag_request_field"),
        (5, "wrong_group_properties", "flag_request_field"),
        (6, "omit_groups", "flag_request_field"),
        (7, "false_geoip_lost", "flag_request_field"),
        (8, "omit_geoip", "flag_request_field"),
        (9, "wrong_scope", "flag_request_field"),
        (10, "startup_flags", "flag_request_count"),
        (11, "startup_flags", "flag_request_count"),
        (11, "capture_flags", "flag_request_count"),
        (12, "unexpected_cache", "flag_request_count"),
        (13, "wrong_parse", "flag_value"),
        (14, "no_retry", "flag_request_count"),
        (15, "no_retry", "flag_request_count"),
        (16, "no_tracking", "flag_event_count"),
        (16, "tracking_key", "flag_event_property"),
        (16, "tracking_value", "flag_event_property"),
    ],
)
async def test_distinct_native_defects_fail_observed_layer(contracts, tmp_path, index, defect, code):
    async with serve(contracts, host_type=RemoteFlagsHost, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    save_receipt(tmp_path, report, diagnostics)
    row = report["results"][index]["result"]
    assert row["status"] == "failed_assertion", report
    assert row["failure"]["code"] == code, row
    assert row["failure"]["failed_step"] and row["failure"]["call_ids"]
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "options,index,status",
    [
        ({"missing_route": "/get_feature_flag"}, 0, "unsupported_binding"),
        ({"missing_route": "/setup"}, 0, "unsupported_binding"),
        ({"missing_route": "/flush"}, 11, "unsupported_binding"),
        ({"missing_capability": "storage.empty.v1"}, 0, "blocked_fixture"),
        ({"sdk_capabilities": []}, 0, "unsupported_binding"),
        ({"cached": True}, 12, "unsupported_binding"),
    ],
)
async def test_native_and_fixture_gaps_are_not_manufactured(contracts, options, index, status):
    async with serve(contracts, host_type=RemoteFlagsHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    assert report["results"][index]["result"]["status"] == status
    assert not host.inputs and not host.fixtures


async def test_caching_server_single_reads_and_lifecycle_still_apply(contracts):
    async with serve(contracts, host_type=RemoteFlagsHost, cached=True) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert [i for i, r in enumerate(report["results"]) if r["result"]["status"] == "passed"] == [
        i for i in range(17) if i != 12
    ]
    assert report["results"][12]["result"]["status"] == "not_selected"
    for index in (10, 11):
        assert selection(CASES[index], host.profile, host.routes)["selected"]


async def test_wire_capability_is_independent_of_sdk_type_and_callback_applicability(contracts):
    async with serve(contracts, host_type=RemoteFlagsHost, runtime="browser", sdk_type="client") as (host, url):
        assert selection(CASES[0], host.profile, host.routes)["selected"]
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert all(r["result"]["status"] == "not_applicable" for r in report["results"])
    assert not host.fixtures
    async with serve(contracts, host_type=RemoteFlagsHost) as (host, url):
        report, _ = await run(contracts, SPECS, [CALLBACK_FEATURE], url, host.profile["id"])
    assert all(r["result"]["status"] == "not_applicable" for r in report["results"])


async def test_api_key_alias_is_accepted_by_real_http(contracts):
    async with serve(contracts, host_type=RemoteFlagsHost, defect="api_key_alias") as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[4]])
    assert strict_exit_code(contracts, report) == 0


async def test_three_canonical_callback_scenarios_separate_from_yaml_origins(contracts, tmp_path):
    async with serve(contracts, host_type=RemoteFlagsHost, runtime="browser", sdk_type="client") as (host, url):
        report, diagnostics = await run(contracts, SPECS, [CALLBACK_FEATURE], url, host.profile["id"])
    save_receipt(tmp_path, report, diagnostics)
    assert strict_exit_code(contracts, report) == 0, report
    assert len(report["results"]) == 3
    assert not any(d["network"] for d in diagnostics["cases"])
    callbacks = [o for f in host.fixtures.values() for o in f.observations if o["kind"] == "callback"]
    assert [len(o["args"]) for o in callbacks] == [3, 2]
    assert callbacks[0]["args"][2] == {"kind": "value", "value": {}}
    assert all(o["owner_call_id"] for o in callbacks)


@pytest.mark.parametrize(
    "defect,index,code",
    [("missing_immediate", 1, "flag_callback_missing"), ("ignored_unsubscribe", 2, "flag_callback_unsubscribe")],
)
async def test_native_callback_defects(contracts, defect, index, code):
    cases, _ = load_cases(SPECS, [CALLBACK_FEATURE])
    async with serve(contracts, host_type=RemoteFlagsHost, runtime="browser", sdk_type="client", defect=defect) as (
        host,
        url,
    ):
        report, _ = await run(contracts, SPECS, [CALLBACK_FEATURE], url, host.profile["id"], case_ids=[cases[index].id])
    assert report["results"][index]["result"]["failure"]["code"] == code


async def initialize_fixture(client, host, server):
    fixture = await client.allocate("flags-fixture", "component-case", host.profile["id"])
    await fixture.invoke("setup", "/setup", {"project_token": "test-token", "config": {"host": server.url}})
    return fixture


def callback_plan(**changes):
    return {
        "signature": "on_feature_flags",
        "max_invocations": 10,
        "calls": [],
        "returns": {"source": "literal", "outcome": {"kind": "void"}},
        **changes,
    }


async def test_client_native_http_load_callback_barrier_cached_reads_and_error_context(contracts, tmp_path):
    server = CaseServer()
    server.set_flags("controlled-client", {"enabled": True, "variant": "blue", "disabled": False, "empty": ""}, {})
    try:
        async with serve(contracts, host_type=RemoteFlagsHost, runtime="browser", sdk_type="client") as (host, url):
            async with Client(url, contracts) as client:
                fixture = await initialize_fixture(client, host, server)
                ref = await fixture.reference("listener", {"kind": "callback", "plan": callback_plan()})
                await fixture.invoke("register", "/on_feature_flags", {}, references={"/callback": ref})
                assert not [o for o in await fixture.observe() if o["kind"] == "callback"]
                await fixture.invoke("load", "/reload_feature_flags", {})
                loaded = [o for o in await fixture.observe() if o["kind"] == "callback"]
                assert len(loaded) == 1
                assert loaded[0]["args"] == [
                    {"kind": "value", "value": ["enabled", "variant"]},
                    {"kind": "value", "value": {"enabled": True, "variant": "blue"}},
                    {"kind": "value", "value": {"errorsLoading": False}},
                ]
                assert len(server.flag_requests()) == 1
                for i in range(2):
                    receipt = await fixture.invoke(
                        f"read-{i}", "/get_feature_flag", {"key": "variant", "send_event": False}
                    )
                    assert receipt["completion"]["outcome"] == {"kind": "value", "value": "blue"}
                assert len(server.flag_requests()) == 1
                assert host.engines[0].records == []
                server.fail_next_flags(400)
                await fixture.invoke("error-load", "/reload_feature_flags", {})
                errors = [o for o in await fixture.observe() if o["kind"] == "callback"]
                assert errors[0]["args"][-1] == {"kind": "value", "value": {"errorsLoading": True}}
                assert errors[0]["args"][:2] == loaded[0]["args"][:2]
                assert [r.response_status for r in server.flag_requests()] == [200, 400]
                (tmp_path / "callback-http.json").write_text(
                    json.dumps({"observations": fixture.observations, "network": server.requests()}, indent=2)
                )
    finally:
        server.close()


async def test_cached_client_zero_requests_false_absent_and_synchronous_plan_context(contracts):
    server = CaseServer()
    try:
        async with serve(contracts, host_type=RemoteFlagsHost, runtime="browser", sdk_type="client") as (host, url):
            async with Client(url, contracts) as client:
                fixture = await initialize_fixture(client, host, server)
                await fixture.invoke("cache", "/update_flags", {"flags": {"off": False, "on": True}})
                plan = callback_plan(
                    calls=[
                        {
                            "step_id": "read",
                            "route": "/get_feature_flags",
                            "receiver": {"source": "reference", "reference": fixture.receiver},
                            "args": {},
                        }
                    ],
                    returns={"source": "callback_argument", "index": 1},
                )
                ref = await fixture.reference("listener", {"kind": "callback", "plan": plan})
                registered = await fixture.invoke("register", "/on_feature_flags", {}, references={"/callback": ref})
                observed = [o for o in await fixture.observe() if o["kind"] == "callback"]
                assert len(observed[0]["args"]) == 2
                assert observed[0]["args"][1] == {"kind": "value", "value": {"on": True}}
                assert observed[0]["completion"]["outcome"] == observed[0]["args"][1]
                assert host.engines[0].callback_contexts == ["register"]
                nested = client.calls[observed[0]["call_ids"][0]]
                assert nested["parent_call_id"] == "register" and nested["completion"]["outcome"]["value"] == {
                    "off": False,
                    "on": True,
                }
                for key, expected in [("off", False), ("missing", None)]:
                    read = await fixture.invoke(key, "/get_feature_flag", {"key": key, "send_event": False})
                    assert read["completion"]["outcome"] == {"kind": "value", "value": expected}
                await fixture.invoke("subscribed-change", "/update_flags", {"flags": {"on": "changed"}})
                changed = [o for o in await fixture.observe() if o["kind"] == "callback"]
                assert changed[0]["invocation_index"] == 1
                assert changed[0]["args"][1] == {"kind": "value", "value": {"on": "changed"}}
                assert host.engines[0].callback_contexts == ["register", "subscribed-change"]
                subscription = registered["completion"]["outcome"]["value"]
                await fixture.invoke("remove", "/subscription/unsubscribe", {}, receiver=subscription)
                await fixture.invoke("remove-again", "/subscription/unsubscribe", {}, receiver=subscription)
                await fixture.invoke("change", "/update_flags", {"flags": {"on": "new"}})
                assert not [o for o in await fixture.observe() if o["kind"] == "callback"]
                assert not server.requests()
    finally:
        server.close()


async def test_readiness_and_typed_callback_argument_schemas_remain_distinct(contracts):
    schema = contracts.schemas["protocol"]["definitions"]["CallbackArguments"]
    ref = schema["properties"]["on_feature_flags"]["$ref"].split("/")[-1]
    for args in [
        [[], {}],
        [["on"], {"on": True}, {}],
        [["on"], {"on": "blue"}, {"errorsLoading": False}],
        [[], {}, {"errorsLoading": True}],
    ]:
        contracts.validate(ref, args)
    for args in [[], [[], {}, False], [["on"], {"on": 1}], [[], {}, {"errorsLoading": None}]]:
        with pytest.raises(BoundaryError):
            contracts.validate(ref, args)
    assert schema["properties"]["readiness"]["maxItems"] == 0


@pytest.mark.parametrize(
    "wrong", [BASE_CATALOG_HASH, "7b2e0eddfb9c72ac80c938de70bf4025eb58fc1d655d380df42cc7544734f145"]
)
async def test_old_peers_rejected_before_allocation(contracts, wrong):
    async with serve(contracts, host_type=RemoteFlagsHost) as (host, url):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url + "/v2/negotiate",
                json={"contract_version": "2.0.0", "catalog_sha256": wrong, "transport": "http-json-v2"},
            ) as response:
                assert (await response.json())["code"] == "catalog_mismatch"
        assert not host.fixtures


async def test_source_wire_scopes_alias_precedence_python_equality_and_named_event_first():
    def request(body, **kwargs):
        return SimpleNamespace(path="/flags", body_decompressed=json.dumps(body), query_params={"v": "2"}, **kwargs)

    recorded = [request({"api_key": "token", "groups": {}, "geoip_disable": 0}), request({"token": "wrong"})]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: recorded)))
    await field(ctx, None, "token", '"token"')
    await field(ctx, None, "geoip_disable", "false")
    await query(ctx, None, "v", "2")
    recorded[0].body_decompressed = '{"token": "earlier", "token": "token"}'
    await field(ctx, None, "token", '"token"')
    recorded[0].body_decompressed = "not-json"
    with pytest.raises(BoundaryError) as malformed:
        await field(ctx, None, "token", '"token"')
    assert malformed.value.kind == "failed_assertion"

    recorded[0].body_decompressed = '{"token": false, "api_key": "token"}'
    with pytest.raises(BoundaryError):
        await field(ctx, None, "token", '"token"')
    recorded[:] = [SimpleNamespace(path="/flags/extra"), SimpleNamespace(path="/decide")]
    await count(ctx, None, "1")
    recorded[:] = [
        SimpleNamespace(path="/batch", headers={}),
        SimpleNamespace(path="/flags", headers={"authorization": "later"}),
    ]
    await absent_header(ctx, None, "Authorization")
    recorded[0].headers["authorization"] = ""
    with pytest.raises(BoundaryError):
        await absent_header(ctx, None, "Authorization")
    recorded[:] = [
        SimpleNamespace(
            path="/batch",
            parsed_events=[{"event": "E", "properties": {"p": "wrong"}}, {"event": "E", "properties": {"p": "right"}}],
        )
    ]
    with pytest.raises(BoundaryError):
        await event_property(ctx, None, "E", "p", '"right"')
    recorded[0].path = "/other-received-path"
    await event_count(ctx, None, "2", "E")


async def test_supported_local_only_and_send_event_controls_reach_engine(contracts):
    server = CaseServer()
    server.set_flags("user", {"flag": True}, {})
    try:
        async with serve(contracts, host_type=RemoteFlagsHost) as (host, url):
            async with Client(url, contracts) as client:
                fixture = await initialize_fixture(client, host, server)
                args = {"key": "flag", "distinct_id": "user", "only_evaluate_locally": True, "send_event": False}
                local = await fixture.invoke("local", "/get_feature_flag", args)
                assert local["completion"]["outcome"] == {"kind": "value", "value": None}
                assert not server.requests()
                args["only_evaluate_locally"] = False
                remote = await fixture.invoke("remote", "/get_feature_flag", args)
                assert remote["completion"]["outcome"] == {"kind": "value", "value": True}
                assert len(server.flag_requests()) == 1 and not host.engines[0].records
                assert host.inputs[-1]["invoke"]["args"] == args
    finally:
        server.close()


@pytest.mark.parametrize(
    "options,status",
    [
        ({"missing_route": "/on_feature_flags"}, "unsupported_binding"),
        ({"missing_route": "/update_flags"}, "unsupported_binding"),
        ({"missing_capability": "callbacks.continuation"}, "blocked_fixture"),
    ],
)
async def test_callback_native_operation_and_fixture_gaps(contracts, options, status):
    cases, _ = load_cases(SPECS, [CALLBACK_FEATURE])
    async with serve(contracts, host_type=RemoteFlagsHost, runtime="browser", sdk_type="client", **options) as (
        host,
        url,
    ):
        report, _ = await run(contracts, SPECS, [CALLBACK_FEATURE], url, host.profile["id"], case_ids=[cases[0].id])
    assert report["results"][0]["result"]["status"] == status, report


def test_changed_flag_amendment_input_is_rejected(contracts, tmp_path):
    import shutil

    shutil.copytree(CONTRACT_PATH, tmp_path / "contracts", ignore=shutil.ignore_patterns("node_modules"))
    amendment = tmp_path / "contracts/inputs/flag-semantics-v1.ts"
    amendment.write_text(amendment.read_text() + "\n")
    with pytest.raises(BoundaryError) as error:
        Contracts(tmp_path / "contracts")
    assert error.value.code == "catalog_mismatch"


@pytest.mark.parametrize(
    "runtime,sdk_type,feature",
    [
        ("edge", "server", FEATURE),
        ("desktop", "server", FEATURE),
        ("desktop", "client", CALLBACK_FEATURE),
    ],
)
async def test_declared_sdk_type_is_independent_of_runtime(contracts, runtime, sdk_type, feature):
    cases, _ = load_cases(SPECS, [feature])
    async with serve(contracts, host_type=RemoteFlagsHost, runtime=runtime, sdk_type=sdk_type) as (host, url):
        report, _ = await run(contracts, SPECS, [feature], url, host.profile["id"], case_ids=[cases[0].id])
    assert strict_exit_code(contracts, report) == 0, report
    assert report["results"][0]["result"]["status"] == "passed"


@pytest.mark.parametrize("feature", [FEATURE, CALLBACK_FEATURE])
async def test_missing_sdk_type_blocks_selected_scenario_without_inference(contracts, feature):
    cases, _ = load_cases(SPECS, [feature])
    async with serve(contracts, host_type=RemoteFlagsHost, sdk_type=None) as (host, url):
        contracts.validate("ExecutionProfile", host.profile)
        report, _ = await run(contracts, SPECS, [feature], url, host.profile["id"], case_ids=[cases[0].id])
    result = report["results"][0]["result"]
    assert result["status"] == "blocked_contract" and result["failure"]["code"] == "sdk_type_undeclared"
    assert not result["executed"] and not host.inputs and not host.fixtures
