"""Public identify/alias bindings over the existing controlled HTTP host."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import STEPS, run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURES = ["black-box/public/identify.feature", "black-box/public/alias.feature"]
ARGUMENTS = {
    "identify": {"distinct_id": "user-123", "set": {"active": False, "score": 0, "note": None}},
    "alias": {"distinct_id": "anon-123", "alias": "user-123"},
}
EVENT_NAMES = {"identify": "$identify", "alias": "$create_alias"}


class IdentifyAliasHost(AnalyticsWireHost):
    """Translate the two public operations; reuse admission, flush and transport."""

    def __init__(self, contracts, **options):
        super().__init__(contracts, **options)
        self.routes.extend(r for r in ("/identify", "/alias") if r != options.get("missing_route"))

    async def invoke(self, fixture, call):
        route, args = call["route"], call["args"]
        if route not in ("/identify", "/alias"):
            return await super().invoke(fixture, call)
        if fixture.defect == "operation_throw":
            raise RuntimeError("Controlled operation failure")
        identity = args["distinct_id"]
        if route == "/identify":
            properties = {"$set": args["set"]}
        else:
            properties = {"alias": args["alias"]}
            if fixture.defect == "reversed_alias":
                identity, properties["alias"] = properties["alias"], identity
        await fixture.engine.capture(
            {"event": EVENT_NAMES[route[1:]], "distinct_id": identity, "properties": properties}
        )


def public_feature(operation):
    args = ARGUMENTS[operation]
    property_assertion = (
        '"$set" should equal JSON ' + json.dumps(args["set"])
        if operation == "identify"
        else '"alias" should equal "user-123"'
    )
    return f'''@public @black_box @sdk:server
Feature: Public {operation} regression
  @case:regression:{operation}
  Scenario: Deliver the public operation
    Given an isolated SDK with empty persistent storage
    And the SDK is initialized with token "test-token" and flush threshold 20
    When {operation} is called with JSON arguments:
      """application/json
      {json.dumps(args)}
      """
    And pending captures are flushed
    Then exactly 1 capture request should have been received
    And the first request should contain exactly 1 parsed events
    And the first received event field "event" should equal "{EVENT_NAMES[operation]}"
    And the first received event field "distinct_id" should equal "{args['distinct_id']}"
    And the first received event property {property_assertion}
'''


@pytest.fixture
def regression_source(tmp_path):
    for operation in ARGUMENTS:
        (tmp_path / f"{operation}.feature").write_text(public_feature(operation))
    return tmp_path


@pytest.mark.parametrize("operation", ARGUMENTS)
async def test_public_operation_delivers_exact_event(regression_source, operation):
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost) as (host, url):
        report, diagnostics = await run(contracts, regression_source, [f"{operation}.feature"], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    assert report["results"][0]["result"]["status"] == "passed"
    assert [call["route"] for call in host.inputs] == ["/setup", f"/{operation}", "/flush"]
    assert json_equal(host.inputs[1]["args"], ARGUMENTS[operation])
    assert len(host.closed) == 1
    assert len(diagnostics["cases"][0]["ingestion"]) == 1


COMMON_DEFECTS = [
    ("wrong_event", "event_field"),
    ("omit_event:event", "event_field"),
    ("numeric_identity", "event_field"),
    ("omit_event:distinct_id", "event_field"),
    ("no_delivery", "request_count"),
    ("empty_batch", "batch_count"),
    ("append_duplicate_uuid", "batch_count"),
    ("duplicate_request", "request_count"),
    ("operation_throw", "unexpected_throw"),
]


@pytest.mark.parametrize(
    "operation,defect,code",
    [(op, defect, code) for op in ARGUMENTS for defect, code in COMMON_DEFECTS]
    + [
        ("identify", "missing_property:$set", "event_property"),
        ("identify", 'property_value:0:$set:{"active":0,"score":0,"note":null}', "event_property"),
        ("identify", 'property_value:0:$set:{"active":false,"score":false,"note":null}', "event_property"),
        ("identify", 'property_value:0:$set:{"active":false,"score":0}', "event_property"),
        ("identify", 'property_value:0:$set:{"active":false,"score":0,"note":false}', "event_property"),
        ("alias", "missing_property:alias", "event_property"),
        ("alias", 'property_value:0:alias:"wrong"', "event_property"),
        ("alias", "reversed_alias", "event_field"),
    ],
)
async def test_public_operation_rejects_wire_defects(regression_source, operation, defect, code):
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost, defect=defect) as (host, url):
        report, _ = await run(contracts, regression_source, [f"{operation}.feature"], url, host.profile["id"])
    result = report["results"][0]["result"]
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == f"{operation}.feature"
    assert len(result["failure"]["call_ids"]) == (2 if defect == "operation_throw" else 3)
    if defect == "operation_throw":
        assert [call["route"] for call in host.inputs] == ["/setup", f"/{operation}"]
        outcome = report["calls"][-1]["completion"]["outcome"]
        assert outcome["kind"] == "thrown"
        assert outcome["error"]["message"] == "Controlled operation failure"
    assert len(host.closed) == 1
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("operation", ARGUMENTS)
async def test_missing_public_operation_is_not_silently_excluded(regression_source, operation):
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost, missing_route=f"/{operation}") as (host, url):
        report, _ = await run(contracts, regression_source, [f"{operation}.feature"], url, host.profile["id"])
    result = report["results"][0]["result"]
    assert result["status"] == "unsupported_binding", report
    assert result["failure"]["code"] == "missing_operation"
    assert not result["executed"] and not host.fixtures and not host.inputs
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("operation", ARGUMENTS)
async def test_server_cases_do_not_run_on_client_profile(regression_source, operation):
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost, runtime="browser") as (host, url):
        report, _ = await run(contracts, regression_source, [f"{operation}.feature"], url, host.profile["id"])
    assert report["results"][0]["result"]["status"] == "not_applicable", report
    assert not host.inputs and not host.fixtures
    assert strict_exit_code(contracts, report) == 1  # An entirely inapplicable scope cannot pass.


@pytest.mark.parametrize(
    "properties,expected,passes",
    [
        ({"value": False}, False, True),
        ({"value": 0}, 0, True),
        ({"value": None}, None, True),
        ({"value": True}, 1, False),
        ({"value": 1}, True, False),
        ({"value": 0}, False, False),
        ({"value": False}, 0, False),
        ({}, None, False),
        (None, None, False),
        ([], None, False),
        ({"value": {"nested": [0, None]}}, {"nested": [False, None]}, False),
        ({"value": {"nested": [False]}}, {"nested": [False, None]}, False),
    ],
)
async def test_received_property_json_preserves_types_and_presence(properties, expected, passes):
    observed = [SimpleNamespace(parsed_events=[{"properties": properties}])]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(
        text='the first received event property "value" should equal JSON ' + json.dumps(expected),
        argument={},
        source={"path": "property.feature", "line": 1},
    )
    handler, args = STEPS.bind(step)
    if passes:
        await handler(ctx, step, *args)
    else:
        with pytest.raises(BoundaryError) as error:
            await handler(ctx, step, *args)
        assert error.value.code == "event_property"


async def test_companion_identify_alias_features_through_public_http(specs):
    cases, _ = load_cases(specs, FEATURES)
    assert {case.id for case in cases} == {
        "black-box:server:identify:scalar-values",
        "black-box:server:identify:nested-values",
        "black-box:server:alias:signup",
        "black-box:server:alias:second-person",
    }
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost) as (host, url):
        report, _ = await run(contracts, specs, FEATURES, url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    assert [row["result"]["status"] for row in report["results"]] == ["passed"] * 4
    assert len(host.closed) == 4
    assert [call["route"] for call in host.inputs] == [
        route for case in cases for route in ("/setup", "/alias" if "alias" in case.id else "/identify", "/flush")
    ]
