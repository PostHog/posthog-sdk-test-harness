"""Fail-closed SDK outcomes and saved-report status mutations without companion specs."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.concurrent import CAPABILITY, invoke_concurrent
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.report import strict_exit_code, validate_report
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import Context
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve


@pytest.fixture
def empty_flush_feature(tmp_path):
    (tmp_path / "empty.feature").write_text(
        """Feature: Empty queue
 Scenario: No requests
  Given an isolated SDK with empty persistent storage
  And the SDK is initialized with token "fixture-token" and flush threshold 20
  When pending captures are flushed
  Then exactly 0 capture request should have been received
"""
    )
    return tmp_path


class ThrowingSetupHost(AIHost):
    async def invoke(self, fixture, call):
        if call["route"] == "/setup":
            raise RuntimeError("Native setup failed")
        return await super().invoke(fixture, call)


@pytest.mark.parametrize("host_type,expected", [(AIHost, "passed"), (ThrowingSetupHost, "failed_assertion")])
async def test_setup_throw_cannot_pass_zero_request_assertion(empty_flush_feature, host_type, expected):
    contracts = Contracts()
    async with serve(contracts, host_type=host_type) as (host, url):
        report, diagnostics = await run(contracts, empty_flush_feature, ["empty.feature"], url, host.profile["id"])
    result = report["results"][0]["result"]
    assert result["status"] == expected
    assert diagnostics["cases"][0]["network"] == []
    assert strict_exit_code(contracts, report) == (0 if expected == "passed" else 1)
    if expected != "passed":
        assert result["failure"]["code"] == "unexpected_throw"
        assert result["failure"]["failed_step"]["index"] == 1
        assert [call["route"] for call in report["calls"]] == ["/setup"]
        assert report["calls"][0]["completion"]["outcome"]["kind"] == "thrown"


@pytest.mark.parametrize("route", ["/setup", "/capture", "/capture_ai", "/identify", "/flush"])
@pytest.mark.parametrize("check_result", [True, False])
async def test_context_checks_native_throws_only_when_requested(route, check_result):
    outcome = {"kind": "thrown", "error": {"message": "native failure"}}
    context = Context(None, SimpleNamespace(steps=[SimpleNamespace(source={})]), {}, None, 5000, {"invocations": []})
    context.fixture = SimpleNamespace(
        id="fixture", invoke=AsyncMock(return_value={"completion": {"kind": "sdk", "outcome": outcome}})
    )
    if check_result:
        with pytest.raises(BoundaryError) as error:
            await context.call(route, {}, check_result=check_result)
        assert (error.value.kind, error.value.code) == ("failed_assertion", "unexpected_throw")
    else:
        assert await context.call(route, {}, check_result=check_result) == outcome
    assert context.last_receipt["completion"]["outcome"] == outcome


@pytest.mark.parametrize("include_call_ids", [False, True])
async def test_failed_report_cannot_be_relabelled_passed(empty_flush_feature, include_call_ids):
    contracts = Contracts()
    async with serve(contracts, host_type=ThrowingSetupHost) as (host, url):
        report, diagnostics = await run(contracts, empty_flush_feature, ["empty.feature"], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 1
    result = report["results"][0]["result"]
    result["status"] = "passed"
    if include_call_ids:
        result["call_ids"] = result["failure"]["call_ids"]
    with pytest.raises(BoundaryError, match="Invalid fields for passed"):
        contracts.validate("Report", report)
    with pytest.raises(BoundaryError, match="Invalid fields for passed"):
        validate_report(contracts, report)
    assert strict_exit_code(contracts, report) == 2
    path = empty_flush_feature / "report.json"
    path.write_text(json.dumps(report))
    path.with_name("report.json.diagnostics.json").write_text(json.dumps(diagnostics))
    checked = CliRunner().invoke(main, ["check-report", "--report", str(path), "--profile", host.profile["id"]])
    assert checked.exit_code != 0
    assert "Malformed compliance report" in checked.output


@pytest.mark.parametrize(
    "result",
    [
        {"status": "passed", "executed": True, "call_ids": ["call"]},
        {"status": "not_selected", "executed": False, "reason": "Not selected"},
        {"status": "not_applicable", "executed": False, "reason": "SDK type", "applicability_rule": "sdk-type"},
        *[
            {
                "status": status,
                "executed": False,
                "failure": {"code": "unavailable", "message": "Unavailable", "failed_step": None, "call_ids": []},
            }
            for status in (
                "failed_assertion",
                "blocked_fixture",
                "blocked_contract",
                "unsupported_binding",
                "harness_error",
            )
        ],
    ],
)
def test_result_status_requires_its_own_fields_and_forbids_other_fields(result):
    contracts = Contracts()
    contracts.result(result)
    for field in result:
        broken = deepcopy(result)
        del broken[field]
        with pytest.raises(BoundaryError):
            contracts.result(broken)
    for field in {"failure", "call_ids", "reason", "applicability_rule"} - result.keys():
        with pytest.raises(BoundaryError):
            contracts.result({**result, field: None})


@pytest.mark.parametrize("advertised", [False, True])
async def test_concurrent_invocation_remains_blocked_even_when_advertised(advertised):
    fixture = SimpleNamespace(invoke=AsyncMock())
    with pytest.raises(BoundaryError) as error:
        await invoke_concurrent(
            fixture, {"fixture_capabilities": [CAPABILITY] if advertised else []}, [{"route": "/capture"}], 5000
        )
    assert (error.value.kind, error.value.code) == ("blocked_fixture", "fixture_unavailable")
    fixture.invoke.assert_not_called()
