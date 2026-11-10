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
            "handled": True,
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
        ("missing_property:handled", "event_property"),
        ("property_value:0:handled:1", "event_property"),
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


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
async def test_normalized_exception_accepts_unknown_stack(specs, protocol):
    case_id = "acceptance:server:capture-exception:normalized:string"
    exceptions = [
        {
            "type": "Error",
            "value": "thrown string",
            "mechanism": {"type": "generic", "handled": True, "synthetic": True, "exception_id": 0},
        }
    ]
    contracts = Contracts()
    async with serve(
        contracts,
        host_type=CaptureExceptionHost,
        protocol=protocol,
        defect="property_value:0:$exception_list:" + json.dumps(exceptions),
    ) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_id])
    assert strict_exit_code(contracts, report) == 0, report
    assert next(row["result"]["status"] for row in report["results"] if row["case_id"] == case_id) == "passed"
    assert len(host.closed) == 1


def tree_fixture(aggregate, identifiers):
    if aggregate:
        name = "Server exception capture attaches aggregate members to their parent"
        entries = [("AggregateError", "group"), ("TypeError", "first member"), ("RangeError", "second member")]
        source = "member"
    else:
        name = "Server exception capture orders a wrapper before its root cause"
        entries = [("TypeError", "wrapper"), ("Error", "root cause")]
        source = "cause"
    exceptions = []
    for index, ((type_name, message), identifier) in enumerate(zip(entries, identifiers, strict=True)):
        mechanism = {"exception_id": identifier, "synthetic": False}
        mechanism.update(
            {"type": "generic", "handled": True}
            if index == 0
            else {"type": "chained", "source": source, "parent_id": identifiers[0]}
        )
        exceptions.append(
            {
                "type": type_name,
                "value": message,
                "mechanism": mechanism,
                "stacktrace": {"frames": [{"function": "report_exception"}]},
            }
        )
    return FEATURE + "::" + name, exceptions


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize(
    "aggregate,identifiers",
    [(False, [0, 1]), (False, [0, 42]), (True, [0, 1, 2]), (True, [0, 7, 9]), (True, [0.0, 7.0, 9.0])],
)
async def test_exception_accepts_valid_tree_identifiers(specs, protocol, aggregate, identifiers):
    case_id, exceptions = tree_fixture(aggregate, identifiers)
    contracts = Contracts()
    async with serve(
        contracts,
        host_type=CaptureExceptionHost,
        protocol=protocol,
        defect="property_value:0:$exception_list:" + json.dumps(exceptions),
    ) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_id])
    assert strict_exit_code(contracts, report) == 0, report
    assert next(row["result"]["status"] for row in report["results"] if row["case_id"] == case_id) == "passed"
    assert len(host.closed) == 1


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "exception_id", 7),
        (1, "exception_id", 0),
        (2, "exception_id", 1),
        (1, "exception_id", -1),
        (1, "exception_id", 1.5),
        (1, "exception_id", True),
        (1, "exception_id", "1"),
        (1, "exception_id", None),
        (1, "exception_id", ...),
        (1, "parent_id", 99),
        (1, "parent_id", 1),
        (1, "parent_id", 2),
        (1, "parent_id", False),
        (1, "parent_id", ...),
        (0, "parent_id", 0),
    ],
)
async def test_exception_rejects_invalid_tree_identifiers(specs, protocol, index, field, value):
    case_id, exceptions = tree_fixture(True, [0, 1, 2])
    if value is ...:
        del exceptions[index]["mechanism"][field]
    else:
        exceptions[index]["mechanism"][field] = value
    contracts = Contracts()
    async with serve(
        contracts,
        host_type=CaptureExceptionHost,
        protocol=protocol,
        defect="property_value:0:$exception_list:" + json.dumps(exceptions),
    ) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case_id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == "exception_mechanism"
    assert strict_exit_code(contracts, report) == 1
    assert len(host.closed) == 1


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
async def test_all_server_exception_cases_deliver(specs, protocol):
    cases, _ = load_cases(specs, [FEATURE])
    selected = [case for case in cases if "@sdk:server" in case.tags]
    assert len(selected) == 12
    contracts = Contracts()
    async with serve(
        contracts, host_type=CaptureExceptionHost, protocol=protocol, missing_capability="storage.empty.v1"
    ) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], tagged_acceptance=True)
    assert strict_exit_code(contracts, report) == 0, report
    assert len([row for row in report["results"] if row["result"]["status"] == "passed"]) == 12
    assert len(host.closed) == 12


@pytest.mark.parametrize(
    "name,defect,code",
    [
        (
            "Server exception capture preserves existing crash-first stack frames in entry-first order",
            'exception:stacktrace:{"frames":[{"function":"boom"},{"function":"handler"},{"function":"main"}]}',
            "exception_frames",
        ),
        (
            "Server exception capture orders a wrapper before its root cause",
            'property_value:0:$exception_list:[{"type":"TypeError","value":"wrapper"},'
            '{"type":"RangeError","value":"root cause"}]',
            "exception_order",
        ),
        (
            "Server exception capture orders a wrapper before its root cause",
            'property_value:0:$exception_list:[{"type":"TypeError","value":"wrapper",'
            '"mechanism":{"exception_id":0,"handled":true}},{"type":"Error","value":"root cause",'
            '"mechanism":{"type":"chained","source":"cause","exception_id":1,"parent_id":1}}]',
            "exception_mechanism",
        ),
        (
            "Server exception capture drops null-valued custom object properties on the wire",
            "property_value:0:test:null",
            "event_property",
        ),
        (
            "Server manual capture does not trust reserved caller exception metadata",
            'property_value:0:$exception_level:"fatal"',
            "event_property",
        ),
        (
            "Server manual capture does not trust reserved caller exception metadata",
            "property_value:0:$exception_source:null",
            "event_property",
        ),
        (
            "Server exception capture preserves the provenance of an existing error-like object stack",
            'exception:mechanism:{"synthetic":false}',
            "exception_mechanism",
        ),
        (
            "Server exception capture preserves existing crash-first stack frames in entry-first order",
            'exception:mechanism:{"handled":true,"synthetic":true}',
            "exception_mechanism",
        ),
        (
            "Server exception capture preserves the provenance of an existing error-like object stack",
            'exception:mechanism:{"synthetic":1}',
            "exception_mechanism",
        ),
        (
            "Server exception capture preserves existing crash-first stack frames in entry-first order",
            'exception:mechanism:{"handled":true,"synthetic":0}',
            "exception_mechanism",
        ),
    ],
)
async def test_expanded_exception_assertions_reject_defects(specs, name, defect, code):
    cases, _ = load_cases(specs, [FEATURE])
    case = next(case for case in cases if case.name == name)
    contracts = Contracts()
    async with serve(contracts, host_type=CaptureExceptionHost, defect=defect) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "value,handled,code",
    [
        (None, False, "exception_value"),
        ("wrong message", False, "exception_message"),
        ("root cause", True, "exception_mechanism"),
    ],
)
async def test_nested_exception_values_and_unknown_handled_state(specs, value, handled, code):
    cases, _ = load_cases(specs, [FEATURE])
    case = next(
        case for case in cases if case.name == "Server exception capture orders a wrapper before its root cause"
    )
    exceptions = [
        {"type": "TypeError", "value": "wrapper", "mechanism": {"exception_id": 0, "handled": True}},
        {
            "type": "Error",
            "value": value,
            "mechanism": {"type": "chained", "source": "cause", "exception_id": 1, "parent_id": 0},
        },
    ]
    if handled:
        exceptions[1]["mechanism"]["handled"] = True
    defect = "property_value:0:$exception_list:" + json.dumps(exceptions)
    contracts = Contracts()
    async with serve(contracts, host_type=CaptureExceptionHost, defect=defect) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
    assert strict_exit_code(contracts, report) == 1


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


async def test_canonical_acceptance_selects_only_opted_in_cases(specs):
    from tests.v2_snapshot_host import SnapshotHost

    contracts = Contracts()
    paths = [path for path in acceptance_paths(specs) if path.startswith("acceptance/")]
    async with serve(contracts, host_type=SnapshotHost) as (host, url):
        report, _ = await run(contracts, specs, paths, url, host.profile["id"], tagged_acceptance=True)
    assert strict_exit_code(contracts, report) == 0, report
    cases, _ = load_cases(specs, paths)
    selected_ids = {case.id for case in cases if "@sdk:server" in case.tags}
    assert len(selected_ids) == 41
    assert all(
        row["result"]["status"] == ("passed" if row["case_id"] in selected_ids else "not_selected")
        for row in report["results"]
    ), report
    assert len(host.closed) == 41
