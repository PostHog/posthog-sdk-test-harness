"""Pinned four local YAML cases through public operations and genuine HTTP loading."""

import hashlib
import json
import subprocess
import time
from copy import deepcopy
from types import SimpleNamespace

import aiohttp
import pytest
import yaml

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import BASE_CATALOG_HASH, BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.fixtures import CaseServer, FlushControls
from posthog_test_harness.v2.flag_fixtures import PROVENANCE_CAPABILITY, FlagStateControls
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, migration_manifest, selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import save_receipt
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_flush_host import serve
from tests.v2_local_parity_host import OWNER, LocalParityEngine, LocalParityHost

FEATURE = SUITE + "/local-evaluation-v1.feature"
SOURCE = "contracts/feature_flags_local_evaluation_tests.yaml"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]
ORIGINS = [
    r
    for line in (SPECS / "coverage/harness-v2/legacy-cases.jsonl").read_text().splitlines()
    if (r := json.loads(line))["source"]["path"] == SOURCE
]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def inputs(origin, action):
    return [s["input"].get("params", {}) for s in origin["steps"] if s["input"]["action"] == action]


def setup_args(params):
    config = {"secret_key": params["personal_api_key"]} if "personal_api_key" in params else {}
    return {"project_token": params.get("api_key", "phc_test_key"), "config": config}


def test_exact_pinned_typed_inputs_assertions_and_source_helpers():
    assert len(CASES) == len(ORIGINS) == 4
    assert sum(len(inputs(o, "get_feature_flag")) for o in ORIGINS) == 119
    assert sum(len(inputs(o, "reload_feature_flag_definitions")) for o in ORIGINS) == 8
    manifest = migration_manifest(SPECS)
    harness = CONTRACT_PATH.parents[2] / "harness"
    for source in manifest["local_evaluation_sources"]:
        data = subprocess.check_output(["git", "-C", str(harness), "show", source["revision"] + ":" + source["path"]])
        assert hashlib.sha256(data).hexdigest() == source["sha256"]
        if source["path"] == SOURCE:
            parsed = yaml.safe_load(data)["categories"]["versioned_boolean_matching"]["tests"]
            assert [p["steps"] for p in parsed] == [[s["input"] for s in o["steps"]] for o in ORIGINS]
    for case, origin in zip(CASES, ORIGINS):
        assert case.migration["legacy_id"] == origin["id"]
        assert case.migration["legacy_source"] == origin["source"]
        assert case.migration["legacy_filters"] == origin["capability_filters"]
        assert case.migration["sdk_capabilities"] == ["feature_flags_local_evaluation_v1"]
        assert case.migration["native_sdk_evidence"] == []
        assert len(case.steps) == len(origin["steps"]) + 2
        for step, source in zip(case.steps[2:], origin["steps"]):
            action = source["input"]["action"]
            params = source["input"].get("params", {})
            if action in ("configure_local_evaluation_definitions", "get_feature_flag"):
                assert json_equal(json_arguments(step), params)
            elif action == "init":
                assert json_equal(json_arguments(step), setup_args(params))
            elif action == "reload_feature_flag_definitions":
                assert params == {"timeout_ms": 5000}
                assert step.text == "local definitions are publicly reloaded and freshly ready within 5000 milliseconds"
            elif action == "assert_action_result":
                assert params["field"] == "value"
                assert step.text == "the local flag getter should return JSON " + json.dumps(params["expected"])
            elif action == "assert_flags_request_count":
                assert step.text == "exactly 0 requests containing /flags should have been received"
            else:
                assert action == "assert_no_requests_to_paths" and params == {"paths": ["/flags", "/flags/"]}
                assert step.text == "no remote flag evaluation path should have been requested"
    discovery = discover(SPECS, [FEATURE])
    assert all(c["status"] == "harness_ready" for c in discovery["cases"])


@pytest.mark.parametrize("path", ["/flags/definitions", "/api/feature_flag/local_evaluation/"])
async def test_all_119_values_eight_fresh_authenticated_barriers_and_owned_observations(contracts, tmp_path, path):
    async with serve(contracts, host_type=LocalParityHost, definition_path=path) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    save_receipt(tmp_path, report, diagnostics)
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 4
    getters = [c for c in report["calls"] if c["route"] == "/get_feature_flag"]
    actual = [c["completion"]["outcome"]["value"] for c in getters]
    expected = [p["expected"] for o in ORIGINS for p in inputs(o, "assert_action_result")]
    assert json_equal(actual, expected) and len(actual) == 119
    assert actual[-5:] == [True, False, True, False, True]
    reloads = [r for d in diagnostics["cases"] for r in d["definition_reloads"]]
    assert len(reloads) == 8
    assert all(0 < r["elapsed_ms"] < r["deadline_ms"] == 5000 for r in reloads)
    assert all(r["requests"][0]["authenticated"] and r["requests"][0]["status"] == 200 for r in reloads)
    assert all(r["requests"][0]["path"] == path for r in reloads)
    assert [r["requests"][0]["body"].get("property_matching_version") for r in reloads] == [
        None,
        1,
        2,
        1,
        2,
        1,
        2,
        None,
    ]
    observations = [
        c["response"]
        for d in diagnostics["cases"]
        for c in d["controls"]
        if c["request"]["command"]["kind"] == "evaluation_provenance"
    ]
    assert len(observations) == 119
    for observed, call, value in zip(observations, getters, actual):
        assert observed["fixture_id"] == call["fixture_id"]
        assert observed["observation"]["call_id"] == call["call_id"]
        assert observed["observation"]["resolution"] == "local"
        assert json_equal(observed["observation"]["value"], value)
    calls = []
    for call in host.inputs:
        invoke = deepcopy(call["invoke"])
        args, route = invoke["args"], invoke["route"]
        if route == "/setup":
            assert args["config"].pop("host").startswith("http://127.0.0.1:")
        contracts.validate(contracts.operations[route]["arguments_schema"].split("/")[-1], args, "catalog")
        calls.append([route, args])
    assert json_equal(
        [args for route, args in calls if route == "/get_feature_flag"],
        [p for o in ORIGINS for p in inputs(o, "get_feature_flag")],
    )
    assert [args for route, args in calls if route == "/setup"] == [setup_args(inputs(o, "init")[0]) for o in ORIGINS]
    assert len(calls) == 4 + 119 + 8 * 2
    assert not any(r["path"].rstrip("/") in ("/flags", "/decide") for d in diagnostics["cases"] for r in d["network"])
    assert len(host.closed) == 4 and all(e.disposed and not e.provenance for e in host.engines)
    assert all(not f.task or f.task.done() for f in host.fixtures.values())
    (tmp_path / "inputs.json").write_text(json.dumps(calls, indent=2))


@pytest.mark.parametrize(
    "index,defect,status,code",
    [
        (0, "no_new_fetch", "failed_assertion", "local_reload_fresh_fetch"),
        (0, "auth_error", "failed_assertion", "local_reload_fresh_fetch"),
        (0, "token_error", "failed_assertion", "local_reload_fresh_fetch"),
        (2, "ignored_version", "failed_assertion", "local_flag_value"),
        (3, "ignored_reload", "failed_assertion", "local_flag_value"),
        (3, "omission_retains_version", "failed_assertion", "local_flag_value"),
        (0, "remote_escape", "failed_assertion", "local_remote_escape"),
        (0, "startup_flags", "failed_assertion", "local_remote_escape"),
        (0, "missing_observation", "blocked_fixture", "local_observation_unavailable"),
        (0, "wrong_call", "harness_error", "invalid_response"),
        (0, "wrong_fixture", "harness_error", "invalid_response"),
        (0, "wrong_key", "failed_assertion", "local_provenance_key"),
        (0, "no_local_result", "failed_assertion", "local_provenance"),
        (0, "wrong_public_value", "failed_assertion", "local_provenance_value"),
        (0, "wrong_bool", "failed_assertion", "local_flag_value"),
        (0, "wrong_string", "failed_assertion", "local_flag_value"),
        (0, "inconclusive", "failed_assertion", "local_inconclusive"),
    ],
)
async def test_defects_fail_at_the_actual_observation_layer(contracts, tmp_path, index, defect, status, code):
    async with serve(contracts, host_type=LocalParityHost, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == status, result
    assert result["failure"]["code"] == code, result
    assert strict_exit_code(contracts, report) == 1
    assert len(host.closed) == 1 and host.engines[0].disposed and not host.engines[0].provenance
    if defect in ("auth_error", "token_error", "no_new_fetch"):
        barrier = diagnostics["cases"][0]["definition_reloads"][0]
        assert barrier["ready"] == {"kind": "value", "value": True}
        assert not barrier["requests"] or barrier["requests"][0]["status"] == 401


@pytest.mark.parametrize("defect", ["reload_timeout", "not_installed"])
async def test_shared_five_second_deadline_cancels_work_and_disposes(contracts, defect):
    started = time.monotonic()
    async with serve(contracts, host_type=LocalParityHost, defect=defect) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    assert 4.8 <= time.monotonic() - started < 10
    assert strict_exit_code(contracts, report) == 1
    assert report["results"][0]["result"]["failure"]["code"] in ("local_reload_deadline", "host_deadline")
    assert len(host.closed) == 1 and host.engines[0].disposed
    assert all(not f.task or f.task.done() for f in host.fixtures.values())


@pytest.mark.parametrize(
    "options,status",
    [
        ({"missing_route": "/get_feature_flag"}, "unsupported_binding"),
        ({"missing_route": "/reload_feature_flags"}, "unsupported_binding"),
        ({"missing_route": "/wait_for_local_evaluation_ready"}, "unsupported_binding"),
        ({"missing_capability": PROVENANCE_CAPABILITY}, "blocked_fixture"),
        ({"sdk_capabilities": []}, "unsupported_binding"),
    ],
)
async def test_independent_native_api_and_fixture_gaps(contracts, options, status):
    async with serve(contracts, host_type=LocalParityHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    assert report["results"][0]["result"]["status"] == status
    assert not host.inputs and not host.fixtures


def test_local_capability_claim_is_not_inferred_from_runtime_wire_or_fixture(contracts):
    for runtime in ("server", "browser", "edge"):
        host = LocalParityHost(contracts, runtime=runtime)
        assert selection(CASES[0], host.profile, host.routes)["selected"]
        assert "sdk_type" not in host.profile
        host.profile["sdk_capabilities"] = ["flags_v2"]
        assert not selection(CASES[0], host.profile, host.routes)["selected"]
    host = LocalParityHost(contracts, missing_route="/get_feature_flag")
    assert selection(CASES[0], host.profile, host.routes)["selected"]


async def test_evaluator_uses_arbitrary_loaded_rules_properties_and_cohorts_not_answers():
    document = deepcopy(inputs(ORIGINS[0], "configure_local_evaluation_definitions")[0]["definitions"])
    engine = LocalParityEngine({}, "unused", {}, "legacy", None, "fixture", "/flags/definitions")
    engine.document = document
    args = deepcopy(inputs(ORIGINS[0], "get_feature_flag")[0])
    flag = document["flags"][0]
    flag["key"] = args["key"] = "new-unlisted-flag"
    args["person_properties"] = {"value": "changed-rule"}
    flag["filters"]["groups"][0]["properties"][0]["value"] = "changed-rule"
    assert engine.evaluate_local(args) is True
    args["person_properties"]["value"] = "different"
    assert engine.evaluate_local(args) is False
    document["cohorts"]["2"]["values"][0]["value"] = "changed-rule"
    args["key"] = "cohort_exact"
    assert engine.evaluate_local(args) is False
    args["person_properties"]["value"] = "changed-rule"
    assert engine.evaluate_local(args) is True
    args["key"] = "group_exact"
    args["groups"] = {"company": "new-company"}
    args["group_properties"] = {"company": {"value": "true"}}
    assert engine.evaluate_local(args) is False
    args["group_properties"]["company"]["value"] = "false"
    assert engine.evaluate_local(args) is True
    args["key"] = "unknown"
    owner = OWNER.set("actual-invocation")
    try:
        assert await engine.get_feature_flag(args) is None
        assert engine.provenance["actual-invocation"]["resolution"] == "fallback"
    finally:
        OWNER.reset(owner)


async def test_provenance_cannot_borrow_another_fixture_or_invocation(contracts):
    server = CaseServer()
    server.state.set_definitions(**inputs(ORIGINS[0], "configure_local_evaluation_definitions")[0])
    try:
        async with serve(contracts, host_type=LocalParityHost) as (host, url):
            async with Client(url, contracts) as client:
                fixtures = []
                for name in ("first", "second"):
                    fixture = await client.allocate(name, "provenance-scope", host.profile["id"])
                    fixtures.append(fixture)
                    await FlushControls(fixture, host.profile, 5000, []).command("storage_empty")
                    args = setup_args(inputs(ORIGINS[0], "init")[0])
                    args["config"]["host"] = server.url
                    await fixture.invoke(name + "-setup", "/setup", args)
                    await fixture.invoke(
                        name + "-getter", "/get_feature_flag", inputs(ORIGINS[0], "get_feature_flag")[0]
                    )
                context = SimpleNamespace(
                    fixture=fixtures[1], profile=host.profile, timeout_ms=5000, diagnostics={"controls": []}
                )
                for call_id in ("first-getter", "second-setup", "missing"):
                    with pytest.raises(BoundaryError) as error:
                        await FlagStateControls(context).command("evaluation_provenance", call_id=call_id)
                    assert error.value.kind == "blocked_fixture"
                observation = await FlagStateControls(context).command("evaluation_provenance", call_id="second-getter")
                assert observation["call_id"] == "second-getter" and observation["resolution"] == "local"
                for fixture in fixtures:
                    await fixture.close()
            assert all(e.disposed and not e.provenance for e in host.engines)
    finally:
        server.close()


async def test_http_304_and_old_public_readiness_do_not_satisfy_fresh_reload(contracts, monkeypatch):
    from flask import request

    from posthog_test_harness.v2 import runner

    class NotModifiedServer(CaseServer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            @self.mock.app.after_request
            def not_modified(response):
                recorded = self.state.get_definition_requests()
                if request.path == "/flags/definitions" and len(recorded) > 1:
                    recorded[-1].response_status = 304
                    response.status_code = 304
                return response

    monkeypatch.setattr(runner, "CaseServer", NotModifiedServer)
    async with serve(contracts, host_type=LocalParityHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    assert report["results"][0]["result"]["failure"]["code"] == "local_reload_fresh_fetch"
    barrier = diagnostics["cases"][0]["definition_reloads"][0]
    assert barrier["ready"] == {"kind": "value", "value": True}
    assert barrier["requests"][0]["status"] == 304
    assert diagnostics["cases"][0]["network"][-1]["status"] == 304


async def test_pre_local_amendment_hash_is_rejected_before_allocation(contracts):
    prior = hashlib.sha256(
        (
            "\n".join([BASE_CATALOG_HASH] + [f'{a["id"]}:{a["sha256"]}' for a in contracts.amendments[:-1]]) + "\n"
        ).encode()
    ).hexdigest()
    async with serve(contracts, host_type=LocalParityHost) as (host, url):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url + "/v2/negotiate",
                json={"contract_version": "2.0.0", "catalog_sha256": prior, "transport": "http-json-v2"},
            ) as response:
                assert (await response.json())["code"] == "catalog_mismatch"
        assert not host.fixtures


async def test_public_entry_cli_runs_all_four_from_outside_checkout(contracts, tmp_path):
    async with serve(contracts, host_type=LocalParityHost) as (host, url):
        code, report, _, output = await cli_run(tmp_path, url, "--feature", FEATURE, "--profile", host.profile["id"])
    assert code == strict_exit_code(contracts, report) == 0, output
    assert len(report["results"]) == 4
