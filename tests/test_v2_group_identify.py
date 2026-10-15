"""Group profile delivery through public calls and received HTTP traffic."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.contracts import Contracts, json_equal
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import STEPS, run
from tests.test_v2_identify_alias import COMMON_DEFECTS
from tests.v2_flush_host import serve
from tests.v2_group_identify_host import GroupIdentifyHost

FEATURE = "acceptance/public/group-identify.feature"


def selected_cases(specs):
    cases, _ = load_cases(specs, [FEATURE])
    return [case for case in cases if "@sdk:server" in case.tags]


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
async def test_companion_group_identify_delivers_exact_events(specs, protocol):
    cases, _ = load_cases(specs, [FEATURE])
    selected = selected_cases(specs)
    assert len(cases) == 6 and len(selected) == 3
    assert all(case.migration is None for case in selected)
    contracts = Contracts()
    async with serve(
        contracts, host_type=GroupIdentifyHost, protocol=protocol, missing_capability="storage.empty.v1"
    ) as (host, url):
        report, diagnostics = await run(contracts, specs, [FEATURE], url, host.profile["id"], tagged_acceptance=True)
    assert strict_exit_code(contracts, report) == 0, report
    assert [row["result"]["status"] for row in report["results"]] == [
        "passed" if "@sdk:server" in case.tags else "not_selected" for case in cases
    ]
    assert [call["route"] for call in host.inputs] == ["/setup", "/group_identify", "/flush"] * 3
    assert len(host.closed) == 3
    args = [call["args"] for call in host.inputs if call["route"] == "/group_identify"]
    assert json_equal(args[0]["properties"], {"plan": "pro", "active": False, "score": 0, "note": None})
    assert json_equal(
        args[1]["properties"],
        {
            "preferences": {"theme": "dark"},
            "tags": ["beta", "team"],
            "$group_type": "literal-type",
            "$group_key": "literal-key",
        },
    )
    assert "properties" not in args[2]
    executed = [case for case in diagnostics["cases"] if case.get("ingestion")]
    assert len(executed) == 3 and all(len(case["ingestion"]) == 1 for case in executed)


@pytest.mark.parametrize(
    "defect,code",
    COMMON_DEFECTS
    + [
        ("missing_property:$group_type", "event_property"),
        ("missing_property:$group_key", "event_property"),
        ('property_value:0:$group_type:"wrong"', "event_property"),
        ('property_value:0:$group_key:"wrong"', "event_property"),
        ("missing_property:$group_set", "event_property"),
        ('property_value:0:$group_set:{"plan":"pro","active":0,"score":0,"note":null}', "event_property"),
        ('property_value:0:$group_set:{"plan":"pro","active":false,"score":false,"note":null}', "event_property"),
        ('property_value:0:$group_set:{"plan":"pro","active":false,"score":0}', "event_property"),
        ("flatten_group_set", "event_property"),
    ],
)
@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
async def test_group_identify_rejects_wire_defects(specs, defect, code, protocol):
    case = selected_cases(specs)[0]
    contracts = Contracts()
    async with serve(contracts, host_type=GroupIdentifyHost, defect=defect, protocol=protocol) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
    assert result["failure"]["call_ids"]
    assert len(report["fixtures"]) == 1
    assert len(host.closed) == 1
    assert strict_exit_code(contracts, report) == 1


async def test_group_identify_rejects_changed_nested_json(specs):
    case = selected_cases(specs)[1]
    altered = {
        "preferences": {"theme": "light"},
        "tags": ["beta", "team"],
        "$group_type": "literal-type",
        "$group_key": "literal-key",
    }
    contracts = Contracts()
    async with serve(
        contracts, host_type=GroupIdentifyHost, defect="property_value:0:$group_set:" + json.dumps(altered)
    ) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion" and result["failure"]["code"] == "event_property", report
    assert strict_exit_code(contracts, report) == 1


async def test_missing_group_identify_route_is_a_visible_gap(specs):
    contracts = Contracts()
    async with serve(contracts, host_type=GroupIdentifyHost, missing_route="/group_identify") as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], tagged_acceptance=True)
    selected = [row["result"] for row in report["results"] if row["result"]["status"] != "not_selected"]
    assert len(selected) == 3
    assert all(result["status"] == "unsupported_binding" for result in selected), report
    assert all(result["failure"]["code"] == "missing_operation" and not result["executed"] for result in selected)
    assert not host.fixtures and not host.inputs
    assert strict_exit_code(contracts, report) == 1


async def test_server_group_cases_are_not_selected_for_client_profile(specs):
    contracts = Contracts()
    async with serve(contracts, host_type=GroupIdentifyHost, runtime="browser") as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], tagged_acceptance=True)
    assert all(row["result"]["status"] == "not_selected" for row in report["results"]), report
    assert not host.fixtures and not host.inputs


@pytest.mark.parametrize(
    "args",
    [
        {"group_type": "company", "group_key": "company-123", "distinct_id": "user-123"},
        {
            "group_type": "company",
            "group_key": "company-123",
            "properties": {"active": False, "score": 0, "note": None},
        },
        {"group_type": "company", "group_key": "company-123", "properties": None, "disable_geoip": False},
    ],
)
async def test_group_identify_binding_forwards_json_unchanged(args):
    calls = []

    async def call(route, value):
        calls.append((route, value))

    ctx = SimpleNamespace(call=call)
    step = SimpleNamespace(
        text="group identify is called with JSON arguments:",
        argument={"docString": {"content": json.dumps(args), "mediaType": "application/json"}},
        source={"path": "group-identify.feature", "line": 1},
    )
    handler, parameters = STEPS.bind(step)
    await handler(ctx, step, *parameters)
    assert len(calls) == 1 and calls[0][0] == "/group_identify"
    assert json_equal(calls[0][1], args)
