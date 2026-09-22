"""Failure evidence supplements, rather than changes, strict result decisions."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.remote_flag_steps import field, result
from posthog_test_harness.v2.runner import failure, failure_diagnostics
from posthog_test_harness.v2.steps import expect


def test_details_are_snapshotted_without_extending_result_or_transport_fields():
    details = {"expected": False, "actual": {"kind": "value", "value": True}, "arguments": {"key": "example"}}
    with pytest.raises(BoundaryError) as caught:
        expect(False, "local_flag_value", "Local getter value differs", details=details)
    details["arguments"]["key"] = "later-call"
    error = caught.value
    source = {"path": "example.feature", "line": 7, "revision": "a" * 64}
    step = {"index": 0, "source": source}
    case = SimpleNamespace(steps=[SimpleNamespace(text="the local flag getter should return JSON false")])
    disposition = failure(error, True, step, ["call-1"])
    Contracts().result(disposition)
    assert set(disposition["failure"]) == {"code", "message", "failed_step", "call_ids"}
    assert set(error.failure()) == {"kind", "code", "message"}
    diagnostic = json.loads(json.dumps(failure_diagnostics(error, step, case)))
    assert diagnostic["failed_step"] == step
    assert diagnostic["step_text"] == case.steps[0].text
    assert diagnostic["details"] == {
        "expected": False,
        "actual": {"kind": "value", "value": True},
        "arguments": {"key": "example"},
    }


def test_failures_without_assertion_details_retain_source_text_when_available():
    error = BoundaryError("missing_operation", "Operation unavailable", "unsupported_binding")
    assert failure_diagnostics(error, None, SimpleNamespace(steps=[])) == {
        "kind": "unsupported_binding",
        "code": "missing_operation",
        "message": "Operation unavailable",
        "failed_step": None,
    }


@pytest.mark.parametrize(
    "body,path,expected,actual",
    [
        ({"geoip_disable": True}, "geoip_disable", "false", {"kind": "value", "value": True}),
        ({"geoip_disable": None}, "geoip_disable", "false", {"kind": "value", "value": None}),
        (
            {"geoip_disable": {"kind": "missing"}},
            "geoip_disable",
            "false",
            {"kind": "value", "value": {"kind": "missing"}},
        ),
        ({}, "geoip_disable", "false", {"kind": "missing"}),
        (
            {"person_properties": None},
            "person_properties.email",
            '"expected"',
            {"kind": "untraversable", "at": "person_properties"},
        ),
    ],
)
async def test_wire_field_diagnostics_distinguish_values_missing_fields_and_untraversable_paths(
    body, path, expected, actual
):
    request = SimpleNamespace(path="/flags/", body_decompressed=json.dumps(body))
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: [request])))
    with pytest.raises(BoundaryError) as caught:
        await field(ctx, None, path, expected)
    assert caught.value.details == {
        "operation": "/flags/",
        "field": path,
        "expected": json.loads(expected),
        "actual": actual,
    }


@pytest.mark.parametrize(
    "outcome",
    [
        {"kind": "undefined"},
        {"kind": "value", "value": {"kind": "undefined"}},
        {"kind": "value", "value": {"kind": "value", "value": None}},
        {"kind": "value", "value": None},
        {"kind": "value", "value": False},
        {"kind": "value", "value": 0},
        {"kind": "value", "value": []},
    ],
)
async def test_getter_diagnostics_preserve_outcome_tags_including_envelope_shaped_json_values(outcome):
    ctx = SimpleNamespace(remote_flag_outcome=outcome, remote_flag_arguments={"key": "example"})
    with pytest.raises(BoundaryError) as caught:
        await result(ctx, None, '"expected-variant"')
    assert caught.value.details["actual"] == outcome
    assert caught.value.details["expected"] == "expected-variant"
