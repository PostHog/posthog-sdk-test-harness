"""Native handled-exception delivery through public calls and observed HTTP."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.contracts import Contracts, json_equal
from posthog_test_harness.v2.discovery import acceptance_paths
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import STEPS, run
from tests.test_v2_identify_alias import COMMON_DEFECTS
from tests.v2_capture_exception_host import CaptureExceptionHost
from tests.v2_flush_host import serve

FEATURE = "acceptance/public/capture-exception.feature"


def delivery_cases(specs):
    cases, _ = load_cases(specs, [FEATURE])
    return [case for case in cases if case.name.startswith("Server exception capture delivers")]


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
async def test_companion_exception_delivers_exact_events(specs, protocol):
    selected = delivery_cases(specs)
    assert len(selected) == 2 and all("@sdk:server" in case.tags for case in selected)
    contracts = Contracts()
    async with serve(
        contracts, host_type=CaptureExceptionHost, protocol=protocol, missing_capability="storage.empty.v1"
    ) as (host, url):
        report, diagnostics = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id for case in selected]
        )
    assert strict_exit_code(contracts, report) == 0, report
    assert len([row for row in report["results"] if row["result"]["status"] == "passed"]) == 2
    assert [call["route"] for call in host.inputs] == ["/setup", "/capture_exception", "/flush"] * 2
    args = [call["args"] for call in host.inputs if call["route"] == "/capture_exception"]
    assert json_equal(
        args[0]["properties"],
        {
            "area": "checkout",
            "retryable": False,
            "attempt": 0,
            "context": {"operation": "charge", "codes": [1, 2], "success": False},
        },
    )
    assert "properties" not in args[1]
    assert len(host.closed) == 2
    executed = [case for case in diagnostics["cases"] if case.get("ingestion")]
    assert len(executed) == 2 and all(len(case["ingestion"]) == 1 for case in executed)


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize(
    "defect,code",
    COMMON_DEFECTS
    + [
        ("missing_property:$exception_list", "exception_list"),
        ("property_value:0:$exception_list:[]", "exception_list"),
        ("property_value:0:$exception_list:null", "exception_list"),
        ("property_value:0:$exception_list:{}", "exception_list"),
        ("property_value:0:$exception_list:[null]", "exception_list"),
        ('exception:type:"Error"', "exception_type"),
        ('exception:value:"wrong"', "exception_message"),
        ("exception:handled:false", "exception_handled"),
        ("exception:handled:1", "exception_handled"),
        ("exception:mechanism:null", "exception_handled"),
        ("exception:stacktrace:null", "exception_stacktrace"),
        ('exception:stacktrace:{"frames":[]}', "exception_stacktrace"),
        ('exception:stacktrace:{"frames":{}}', "exception_stacktrace"),
        ('exception:stacktrace:{"frames":[null]}', "exception_stacktrace"),
        ('exception:stacktrace:{"frames":[{}]}', "exception_stacktrace"),
        (
            'exception:stacktrace:{"frames":[{"filename":"","function":" ","instruction_addr":""}]}',
            "exception_stacktrace",
        ),
        ('exception:stacktrace:{"frames":[{"lineno":42}]}', "exception_stacktrace"),
        ("missing_property:area", "event_property"),
        ("property_value:0:retryable:0", "event_property"),
        ("property_value:0:attempt:false", "event_property"),
        ('property_value:0:context:{"operation":"charge","codes":[2,1],"success":false}', "event_property"),
    ],
)
async def test_exception_rejects_wire_defects(specs, protocol, defect, code):
    case = delivery_cases(specs)[0]
    contracts = Contracts()
    async with serve(contracts, host_type=CaptureExceptionHost, protocol=protocol, defect=defect) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
    assert result["failure"]["call_ids"]
    assert len(host.closed) == 1
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize(
    "frames",
    [
        [{"filename": "fixture.py"}],
        [{"function": "report_exception"}],
        [{"instruction_addr": "0x1234"}],
        [{}, {"instruction_addr": "0x1234"}],
    ],
)
async def test_exception_accepts_frame_locations(specs, protocol, frames):
    case = delivery_cases(specs)[0]
    contracts = Contracts()
    async with serve(
        contracts,
        host_type=CaptureExceptionHost,
        protocol=protocol,
        defect="exception:stacktrace:" + json.dumps({"frames": frames}),
    ) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    assert strict_exit_code(contracts, report) == 0, report
    assert len(host.closed) == 1


async def test_missing_exception_route_is_a_visible_gap(specs):
    selected = delivery_cases(specs)
    contracts = Contracts()
    async with serve(contracts, host_type=CaptureExceptionHost, missing_route="/capture_exception") as (host, url):
        report, _ = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id for case in selected]
        )
    results = [row["result"] for row in report["results"] if row["result"]["status"] != "not_selected"]
    assert len(results) == 2
    assert all(result["status"] == "unsupported_binding" for result in results), report
    assert all(result["failure"]["code"] == "missing_operation" and not result["executed"] for result in results)
    assert not host.fixtures and not host.inputs
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "args",
    [
        {"error": {"type": "TypeError", "message": "boom"}, "distinct_id": "user"},
        {"error": {"type": "TypeError", "message": "boom"}, "properties": {"retryable": False, "attempt": 0}},
        {"error": {"type": "TypeError", "message": "boom"}, "distinct_id": None, "properties": None},
    ],
)
async def test_exception_binding_forwards_json_unchanged(args):
    calls = []

    async def call(route, value):
        calls.append((route, value))

    ctx = SimpleNamespace(call=call)
    step = SimpleNamespace(
        text="capture exception is called with JSON arguments:",
        argument={"docString": {"content": json.dumps(args), "mediaType": "application/json"}},
        source={"path": "capture-exception.feature", "line": 1},
    )
    handler, parameters = STEPS.bind(step)
    await handler(ctx, step, *parameters)
    assert len(calls) == 1 and calls[0][0] == "/capture_exception"
    assert json_equal(calls[0][1], args)


async def test_server_exception_cases_are_not_selected_for_client_profile(specs):
    contracts = Contracts()
    async with serve(contracts, host_type=CaptureExceptionHost, runtime="browser") as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], tagged_acceptance=True)
    assert all(row["result"]["status"] == "not_selected" for row in report["results"]), report
    assert not host.fixtures and not host.inputs


async def test_acceptance_suite_selects_only_opted_in_cases(specs):
    from tests.v2_snapshot_host import SnapshotHost

    contracts = Contracts()
    async with serve(contracts, host_type=SnapshotHost) as (host, url):
        report, _ = await run(
            contracts, specs, acceptance_paths(specs), url, host.profile["id"], tagged_acceptance=True
        )
    assert strict_exit_code(contracts, report) == 0, report
    cases, _ = load_cases(specs, acceptance_paths(specs))
    selected_ids = {case.id for case in cases if "@sdk:server" in case.tags}
    assert len(selected_ids) == 31
    assert all(
        row["result"]["status"] == ("passed" if row["case_id"] in selected_ids else "not_selected")
        for row in report["results"]
    ), report
    assert len(host.closed) == 31
