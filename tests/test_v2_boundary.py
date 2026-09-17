"""Draft2 HTTP envelope, lifecycle, deadline, and report negative controls."""

import asyncio
import json
from copy import deepcopy

import pytest
from aiohttp import web
from click.testing import CliRunner

from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import BoundaryError, Contracts, decode_json, encode_json
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import SPECS
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import Host, serve


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', "NaN", "Infinity", "{", b"\xff"])
def test_strict_json(text):
    with pytest.raises(BoundaryError):
        decode_json(text)


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}, {"null": None, "false": False, "zero": 0}])
def test_typed_preservation(value):
    assert decode_json(encode_json(value)) == value


@pytest.mark.parametrize(
    "kind,value",
    [
        ("void", None),
        ("undefined", None),
        ("value", None),
        ("value", False),
        ("value", 0),
        ("thrown", {"name": "Error", "message": "native"}),
    ],
)
async def test_omission_negative_arguments_and_outcomes(kind, value):
    outcome = {"kind": kind}
    if kind in ("value", "thrown"):
        outcome["error" if kind == "thrown" else "value"] = value

    class Outcomes(Host):
        def outcome(self, call, result):
            return outcome

    async with serve(Contracts(), host_type=Outcomes) as (host, url), Client(url, Contracts()) as client:
        fixture = await client.allocate("f", "case", host.profile["id"])
        args = {"event": None, "properties": False, "timestamp": 0}
        receipt = await fixture.invoke("call", "/capture", args)
        assert host.inputs[0]["args"] == args
        assert "distinct_id" not in host.inputs[0]["args"]
        assert receipt["completion"] == {"kind": "sdk", "outcome": outcome}


@pytest.mark.parametrize(
    "fault,code",
    [("protocol", "incompatible_adapter"), ("duplicate_profile", "invalid_envelope"), ("deadline", "invalid_deadline")],
)
async def test_invalid_negotiation(fault, code):
    class Malformed(Host):
        async def handle(self, request):
            response = await super().handle(request)
            if request.path == "/v2/negotiate":
                data = decode_json(response.body)
                if fault == "protocol":
                    data["protocol"] = "old"
                elif fault == "duplicate_profile":
                    data["profiles"] *= 2
                else:
                    data["max_timeout_ms"] = 60001
                return web.json_response(data)
            return response

    async with serve(Contracts(), host_type=Malformed) as (host, url):
        with pytest.raises(BoundaryError) as error:
            async with Client(url, Contracts()):
                pass
        assert error.value.code == code
        assert not host.fixtures


@pytest.mark.parametrize(
    "fault,code",
    [
        ("http", "http_error"),
        ("attribution", "invalid_response"),
        ("outcome", "invalid_envelope"),
        ("body", "body_limit"),
        ("hang", "transport_timeout"),
    ],
)
async def test_transport_failures_are_not_sdk_throws(fault, code):
    class Malformed(Host):
        async def handle(self, request):
            if request.path == "/v2/invoke":
                if fault == "http":
                    return web.json_response({"error": "bounded cleanup detail"}, status=400)
                if fault == "hang":
                    await asyncio.sleep(1.2)
                if fault == "body":
                    return web.json_response({"padding": "x" * (1024 * 1024)})
                response = await super().handle(request)
                data = decode_json(response.body)
                if fault == "attribution":
                    data["fixture_id"] = "wrong"
                elif fault == "outcome":
                    data["completion"]["outcome"] = {"kind": "value"}
                return web.json_response(data)
            return await super().handle(request)

    async with serve(Contracts(), host_type=Malformed) as (host, url), Client(url, Contracts()) as client:
        fixture = await client.allocate("f", "case", host.profile["id"])
        receipt = await fixture.invoke("call", "/capture", {}, timeout_ms=1)
        assert receipt["completion"]["kind"] == "harness"
        assert receipt["completion"]["failure"]["code"] == code
        if fault == "http":
            assert "bounded cleanup detail" in receipt["completion"]["failure"]["message"]
        assert not fixture.active


async def test_duplicate_call_and_fixture_ids_rejected_before_network():
    async with serve(Contracts()) as (host, url), Client(url, Contracts()) as client:
        fixture = await client.allocate("f", "case", host.profile["id"])
        await fixture.invoke("call", "/capture", {})
        with pytest.raises(BoundaryError, match="Call ID"):
            await fixture.invoke("call", "/capture", {})
        with pytest.raises(BoundaryError, match="Fixture IDs"):
            await client.allocate("f", "case", host.profile["id"])
        assert len(host.inputs) == 1


async def test_cancelled_request_is_not_successful_cancellation():
    class Hanging(Host):
        async def invoke(self, fixture, call):
            await asyncio.sleep(20)

    async with serve(Contracts(), host_type=Hanging) as (host, url), Client(url, Contracts()) as client:
        fixture = await client.allocate("f", "case", host.profile["id"])
        task = asyncio.create_task(fixture.invoke("call", "/capture", {}))
        while not host.inputs:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.calls["call"]["completion"]["failure"]["code"] == "caller_cancelled"
        assert not fixture.active


@pytest.mark.parametrize("defect", ["teardown", "rejected"])
async def test_startup_and_cleanup_failures_cannot_pass(defect):
    async with serve(Contracts(), host_type=AIHost, defect=defect) as (host, url):
        report, _ = await run(
            Contracts(), SPECS, ["migration/yaml-parity-v1/capture-ai.feature"], url, host.profile["id"]
        )
    assert report["errors"]
    assert strict_exit_code(Contracts(), report) == 1


async def test_missing_or_forged_report_cannot_pass():
    contracts = Contracts()
    assert strict_exit_code(contracts, {}) == 2
    async with serve(contracts, host_type=AIHost) as (host, url):
        report, _ = await run(
            contracts, SPECS, ["migration/yaml-parity-v1/capture-ai.feature"], url, host.profile["id"]
        )
    assert strict_exit_code(contracts, report) == 0
    for field in ("results", "calls", "inventory", "fixtures"):
        broken = deepcopy(report)
        broken[field].pop()
        assert strict_exit_code(contracts, broken) == 2
    broken = deepcopy(report)
    broken["results"][0]["result"]["executed"] = False
    assert strict_exit_code(contracts, broken) == 2


async def test_saved_report_gate_checks_results_and_diagnostics(tmp_path):
    async with serve(Contracts(), host_type=AIHost) as (host, url):
        report, diagnostics = await run(
            Contracts(), SPECS, ["migration/yaml-parity-v1/capture-ai.feature"], url, host.profile["id"]
        )
    path = tmp_path / "report.json"
    diagnostic_path = tmp_path / "report.json.diagnostics.json"
    command = ["check-report", "--report", str(path), "--profile", host.profile["id"]]

    def check(saved, details):
        path.write_text(json.dumps(saved))
        if details is None:
            diagnostic_path.unlink(missing_ok=True)
        else:
            diagnostic_path.write_text(json.dumps(details))
        return CliRunner().invoke(main, command).exit_code

    assert check(report, diagnostics) == 0
    multi_report, multi_diagnostics = deepcopy(report), deepcopy(diagnostics)
    other_profile = {**report["profiles"][0], "id": "another-profile"}
    multi_report["profiles"].append(other_profile)
    multi_diagnostics["adapter"]["profiles"].append(other_profile)
    assert check(multi_report, multi_diagnostics) == 0
    assert CliRunner().invoke(main, command[:-1] + ["another-profile"]).exit_code != 0
    assert check({}, diagnostics) != 0
    assert check(report, None) != 0
    assert check(report, {}) != 0
    assert check(report, {**diagnostics, "run_id": "another-run"}) != 0
    assert check(report, {**diagnostics, "cases": []}) != 0
    failed = deepcopy(report)
    failed["errors"].append({"code": "cleanup_failed", "message": "cleanup failed"})
    assert check(failed, diagnostics) == 1
    failed = deepcopy(report)
    failed["results"][0]["result"]["status"] = "failed_assertion"
    assert check(failed, diagnostics) == 1
    check(report, diagnostics)
    assert CliRunner().invoke(main, command[:-1] + ["wrong-profile"]).exit_code != 0


async def test_existing_callback_cases_remain_client_only_for_server_profile():
    async with serve(Contracts(), host_type=AIHost) as (host, url):
        report, _ = await run(
            Contracts(), SPECS, ["acceptance/public/on-feature-flags.feature"], url, host.profile["id"]
        )
    assert len(report["results"]) == 3
    assert all(row["result"]["status"] == "not_applicable" for row in report["results"])
    assert not report["fixtures"]
    assert not report["calls"]
