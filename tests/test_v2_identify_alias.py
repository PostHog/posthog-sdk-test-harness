"""Public identify/alias bindings over the existing controlled HTTP host."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.discovery import acceptance_paths
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import STEPS, run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURES = ["acceptance/public/identify.feature", "acceptance/public/alias.feature"]
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
        if route == "/identify":
            identity = args.get("distinct_id") or str(uuid4())
            properties = {"$set": args["set"]}
            if "distinct_id" not in args:
                properties["$process_person_profile"] = False
                if fixture.defect == "invalid_generated_id":
                    identity = "not-a-uuid"
        else:
            identity = args["distinct_id"]
            properties = {"alias": args["alias"]}
            if fixture.defect == "reversed_alias":
                identity, properties["alias"] = properties["alias"], identity
        event = {"event": EVENT_NAMES[route[1:]], "distinct_id": identity, "properties": properties}
        if fixture.engine.protocol == "analytics_v1":
            event["options"] = {}
            if "$process_person_profile" in properties:
                event["options"]["process_person_profile"] = properties.pop("$process_person_profile")
                if fixture.defect == "missing_personless_option":
                    event["options"].pop("process_person_profile")
        await fixture.engine.capture(event)


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


@pytest.mark.parametrize(
    "path,properties,options,passes",
    [
        ("/batch/", {"$process_person_profile": False}, {}, True),
        ("/i/v1/analytics/events", {}, {"process_person_profile": False}, True),
        ("/batch/", {}, {}, False),
        ("/batch/", {"$process_person_profile": 0}, {}, False),
        ("/i/v1/analytics/events", {}, {"process_person_profile": 0}, False),
        ("/i/v1/analytics/events", {"$process_person_profile": False}, {}, False),
        (
            "/i/v1/analytics/events",
            {"$process_person_profile": False},
            {"process_person_profile": False},
            False,
        ),
        ("/unknown/", {"$process_person_profile": False}, {}, False),
    ],
)
async def test_identify_personless_assertion_checks_observed_transport(path, properties, options, passes):
    observed = [SimpleNamespace(path=path, parsed_events=[{"properties": properties, "options": options}])]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(
        text="the first received identify event disables person-profile processing",
        argument={},
        source={"path": "identify.feature", "line": 1},
    )
    handler, args = STEPS.bind(step)
    if passes:
        await handler(ctx, step, *args)
    else:
        with pytest.raises(BoundaryError) as error:
            await handler(ctx, step, *args)
        assert error.value.code == "identify_personless"


async def test_server_delivery_needs_no_storage_control(regression_source):
    feature = regression_source / "identify.feature"
    feature.write_text(
        feature.read_text().replace("an isolated SDK with empty persistent storage", "an isolated SDK instance")
    )
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost, missing_capability="storage.empty.v1") as (host, url):
        report, _ = await run(contracts, regression_source, ["identify.feature"], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    assert [call["route"] for call in host.inputs] == ["/setup", "/identify", "/flush"]


async def test_companion_identify_alias_features_through_public_http(specs):
    cases, _ = load_cases(specs, FEATURES)
    selected = [case for case in cases if "@sdk:server" in case.tags]
    assert len(cases) == 9  # Unmigrated client and negative cases remain visible.
    assert len(selected) == 5 and all(case.migration is None for case in selected)
    contracts = Contracts()
    async with serve(contracts, host_type=IdentifyAliasHost) as (host, url):
        report, _ = await run(contracts, specs, FEATURES, url, host.profile["id"], tagged_acceptance=True)
    assert strict_exit_code(contracts, report) == 0, report
    assert [row["result"]["status"] for row in report["results"]] == [
        "passed" if "@sdk:server" in case.tags else "not_selected" for case in cases
    ]
    assert len(host.closed) == 5
    assert [call["route"] for call in host.inputs] == [
        route
        for case in selected
        for route in ("/setup", "/alias" if "alias" in case.source["path"] else "/identify", "/flush")
    ]
    assert "distinct_id" not in host.inputs[7]["args"]  # The generated-ID case passes omission through unchanged.


async def test_acceptance_suite_selects_only_opted_in_cases(specs):
    contracts = Contracts()
    paths = acceptance_paths(specs)
    async with serve(contracts, host_type=IdentifyAliasHost) as (host, url):
        report, _ = await run(contracts, specs, paths, url, host.profile["id"], tagged_acceptance=True)
    assert strict_exit_code(contracts, report) == 0, report
    assert len([row for row in report["results"] if row["result"]["status"] == "passed"]) == 5
    assert all(
        row["result"]["status"] == "not_selected" for row in report["results"] if row["source"]["path"] not in FEATURES
    )
    assert len(host.closed) == 5


@pytest.mark.parametrize(
    "defect,code",
    [
        ("invalid_generated_id", "event_field_uuid"),
        ("missing_personless_option", "identify_personless"),
        ("operation_throw", "unexpected_throw"),
    ],
)
async def test_generated_identify_rejects_public_wire_defects(specs, defect, code):
    contracts = Contracts()
    cases, _ = load_cases(specs, [FEATURES[0]])
    generated = next(case for case in cases if case.name.startswith("Server identify without explicit"))
    async with serve(contracts, host_type=IdentifyAliasHost, defect=defect) as (host, url):
        report, _ = await run(contracts, specs, [FEATURES[0]], url, host.profile["id"], case_ids=[generated.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == generated.id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
