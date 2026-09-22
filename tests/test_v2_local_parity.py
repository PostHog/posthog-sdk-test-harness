"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

from copy import deepcopy

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.contracts import Contracts, decode_json
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import save_receipt
from tests.v2_flush_host import serve
from tests.v2_local_parity_host import LocalParityEngine, LocalParityHost

FEATURE = "migration/yaml-parity-v1/local-evaluation-v1.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


@pytest.mark.parametrize(
    "index,defect,status,code",
    [
        (0, "no_initial_fetch", "failed_assertion", "local_initial_fetch"),
        (0, "not_installed", "failed_assertion", "local_inconclusive"),
        (3, "no_new_fetch", "failed_assertion", "local_reload_fresh_fetch"),
        (3, "auth_error", "failed_assertion", "local_reload_fresh_fetch"),
        (3, "token_error", "failed_assertion", "local_reload_fresh_fetch"),
        (2, "ignored_version", "failed_assertion", "local_flag_value"),
        (3, "ignored_reload", "failed_assertion", "local_flag_value"),
        (3, "omission_retains_version", "failed_assertion", "local_flag_value"),
        (0, "remote_escape", "failed_assertion", "local_remote_escape"),
        (0, "startup_flags", "failed_assertion", "local_remote_escape"),
        (0, "wrong_bool", "failed_assertion", "local_flag_value"),
        (0, "wrong_string", "failed_assertion", "local_flag_value"),
        (0, "inconclusive", "failed_assertion", "local_inconclusive"),
    ],
)
async def test_defects_fail_at_the_actual_observation_layer(
    contracts, tmp_path, index, defect, status, code, specs, case_ids, feature_cases
):
    async with serve(contracts, host_type=LocalParityHost, defect=defect) as (host, url):
        report, diagnostics = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[index]]
        )
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == status, result
    assert result["failure"]["code"] == code, result
    assert strict_exit_code(contracts, report) == 1
    evidence = diagnostics["cases"][0]["failure"]
    assert evidence["code"] == code
    assert evidence["failed_step"] == result["failure"]["failed_step"]
    step_index = evidence["failed_step"]["index"]
    step = feature_cases[index].steps[step_index]
    assert evidence["step_text"] == step.text
    if code == "local_flag_value":
        getter = feature_cases[index].steps[step_index - 1]
        actual_call = next(call for call in reversed(report["calls"]) if call["route"] == "/get_feature_flag")
        assert evidence["details"] == {
            "operation": "/get_feature_flag",
            "arguments": json_arguments(getter),
            "expected": decode_json(step.text.removeprefix("the local flag getter should return JSON ")),
            "actual": actual_call["completion"]["outcome"],
        }
    if code == "local_inconclusive":
        assert evidence["details"]["arguments"] == json_arguments(step)
        assert evidence["details"]["expected"] == "conclusive boolean or string value"
        assert evidence["details"]["actual"] == {"kind": "value", "value": None}
    assert len(host.closed) == 1 and host.engines[0].disposed
    if defect in ("auth_error", "token_error", "no_new_fetch"):
        barrier = diagnostics["cases"][0]["definition_reloads"][0]
        assert not barrier["requests"] or barrier["requests"][0]["status"] == 401


async def test_evaluator_uses_arbitrary_loaded_rules_properties_and_cohorts_not_answers(feature_cases):
    document = deepcopy(
        json_arguments(
            next(s for s in feature_cases[0].steps if s.text == "the definitions service serves this typed document:")
        )["definitions"]
    )
    engine = LocalParityEngine({}, "unused", {}, "legacy", None, "fixture", "/flags/definitions")
    engine.document = document
    args = deepcopy(
        json_arguments(
            next(s for s in feature_cases[0].steps if s.text == "the local flag getter is called with JSON arguments:")
        )
    )
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
    assert await engine.get_feature_flag(args) is None


async def test_http_304_and_previously_loaded_definitions_do_not_satisfy_fresh_reload(
    contracts, monkeypatch, specs, case_ids
):
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
        report, diagnostics = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[3]])
    assert report["results"][3]["result"]["failure"]["code"] == "local_reload_fresh_fetch"
    barrier = diagnostics["cases"][0]["definition_reloads"][0]
    assert barrier["requests"][0]["status"] == 304
    assert any(r["status"] == 304 for r in diagnostics["cases"][0]["network"])


async def test_initial_evaluation_can_load_definitions_without_an_explicit_reload(
    contracts, monkeypatch, specs, feature_cases, case_ids
):
    original_getter = LocalParityEngine.get_feature_flag
    calls = []

    async def setup(self):
        pass

    async def get_feature_flag(self, args):
        calls.append(args)
        if self.document is None:
            await self.load_definitions(initial=True)
        return await original_getter(self, args)

    monkeypatch.setattr(LocalParityEngine, "setup", setup)
    monkeypatch.setattr(LocalParityEngine, "get_feature_flag", get_feature_flag)
    async with serve(contracts, host_type=LocalParityHost, missing_route="/reload_feature_flags") as (host, url):
        report, diagnostics = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[0]])
    assert strict_exit_code(contracts, report) == 0, report
    expected_calls = [
        json_arguments(step)
        for step in feature_cases[0].steps
        if step.text == "the local flag getter is called with JSON arguments:"
    ]
    assert calls == expected_calls
    assert host.engines[0].reload_count == 0
    requests = diagnostics["cases"][0]["initial_definitions"]["requests"]
    assert len(requests) == 1 and requests[0]["authenticated"] and requests[0]["status"] == 200


async def test_remote_evaluation_during_public_cleanup_cannot_pass(contracts, specs, case_ids):
    class ShutdownTraffic(LocalParityHost):
        async def handle(self, request):
            if request.path == "/v2/fixtures/close":
                data = await request.json()
                engine = self.fixtures[data["fixture_id"]].engine
                await engine.post("/decide", {"distinct_id": "late"})
            return await super().handle(request)

    async with serve(contracts, host_type=ShutdownTraffic) as (host, url):
        report, diagnostics = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[0]])
    result = report["results"][0]["result"]
    assert result["status"] == "failed_assertion"
    assert result["failure"]["code"] == "local_remote_escape"
    assert diagnostics["cases"][0]["network"][-1]["path"] == "/decide"
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "outcome,code",
    [({"kind": "value", "value": False}, "local_flag_value"), ({"kind": "undefined"}, "local_inconclusive")],
)
async def test_constant_default_and_undefined_results_cannot_satisfy_local_rules(
    contracts, outcome, code, specs, case_ids
):
    class DefaultResult(LocalParityHost):
        def outcome(self, call, result):
            if call["route"] == "/get_feature_flag":
                return outcome
            return super().outcome(call, result)

    async with serve(contracts, host_type=DefaultResult) as (host, url):
        report, diagnostics = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[0]])
    result = report["results"][0]["result"]
    assert result["status"] == "failed_assertion"
    assert result["failure"]["code"] == code
    assert strict_exit_code(contracts, report) == 1
    details = diagnostics["cases"][0]["failure"]["details"]
    assert details["actual"] == outcome
