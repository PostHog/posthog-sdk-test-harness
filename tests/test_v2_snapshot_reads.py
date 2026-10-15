"""Public snapshot semantics, declared representations, and mock-service delivery."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import STEPS, run
from posthog_test_harness.v2.snapshot_steps import semantic_read
from tests.v2_flush_host import serve
from tests.v2_snapshot_host import SnapshotHost

FEATURE = "acceptance/public/evaluate-flags.feature"


def snapshot_cases(specs):
    cases, _ = load_cases(specs, [FEATURE])
    return [case for case in cases if case.name.startswith("Server snapshot")]


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize("value_shape", ["scalar", "rich"])
@pytest.mark.parametrize("payload_shape", ["decoded", "serialized"])
async def test_healthy_snapshot_matrix(specs, protocol, value_shape, payload_shape):
    selected = snapshot_cases(specs)
    assert len(selected) == 21
    contracts = Contracts()
    async with serve(
        contracts, host_type=SnapshotHost, protocol=protocol, value_shape=value_shape, payload_shape=payload_shape
    ) as (host, url):
        report, diagnostics = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id for case in selected]
        )
    assert strict_exit_code(contracts, report) == 0, report
    assert len([row for row in report["results"] if row["result"]["status"] == "passed"]) == 21
    assert len(host.closed) == 21
    assert len([case for case in diagnostics["cases"] if case.get("fixture_id")]) >= 21
    for fixture in host.fixtures.values():
        assert fixture.closed
    assert sum(call["route"] == "/evaluate_flags/read" for call in host.inputs) == 22


DEFECTS = [
    ("reads share", "reorder", "snapshot_enablement"),
    ("reads share", "extra_result", "snapshot_result_count"),
    ("reads share", "coerce_bool", "snapshot_enablement"),
    ("reads share", "no_dedupe", "snapshot_exposure_count"),
    ("reads share", "wrong_exposure", "snapshot_exposure_response"),
    ("reads share", "extra_request", "snapshot_request_count"),
    ("projections distinguish", "missing_error", "snapshot_exposure_missing"),
    ("projections distinguish", "missing_response_true", "snapshot_exposure_response"),
    ("payload reads are silent", "payload_access", "snapshot_exposure_count"),
    ("payload reads are silent", "wrong_payload", "snapshot_payload"),
    ("scalar payload", "drop_falsy", "snapshot_result"),
    ("independent requests", "stale_snapshot", "snapshot_exposure_count"),
    ("forwards explicit", "wrong_context", "snapshot_request_context"),
    ("forwards explicit", "wrong_groups", "snapshot_exposure_groups"),
    ("request-time key scope", "wrong_scope", "snapshot_request_scope"),
    ("empty request-time", "empty_network", "snapshot_request_count"),
    ("explicit-key filters", "filter_all", "snapshot_keys"),
    ("explicit-key filters", "filter_access", "snapshot_exposure_count"),
    ("accessed-key filtering before", "accessed_all", "snapshot_keys"),
    ("accessed-key filtering before", "enumeration_access", "snapshot_keys"),
    ("accessed-key filtering selects", "parent_leak", "snapshot_keys"),
    ("supported caller defaults", "default_overrides", "snapshot_enablement"),
    ("missing identity", "invent_identity", "snapshot_request_count"),
    ("disabled SDK", "empty_network", "snapshot_keys"),
    ("remote failure", "no_evaluation", "snapshot_request_count"),
    ("remote failure", "accessor_retry", "snapshot_request_count"),
]


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize("name,defect,code", DEFECTS)
async def test_defective_snapshot_hosts_fail(specs, protocol, name, defect, code):
    case = next(case for case in snapshot_cases(specs) if name in case.name)
    contracts = Contracts()
    async with serve(contracts, host_type=SnapshotHost, protocol=protocol, defect=defect) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion", report
    assert result["failure"]["code"] == code
    assert result["failure"]["call_ids"] and len(host.closed) == 1
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("shape", ["decoded", "serialized"])
async def test_json_looking_strings_are_not_double_decoded(specs, shape):
    case = next(case for case in snapshot_cases(specs) if case.id == "server_snapshot_payload_json_looking_string")
    contracts = Contracts()
    async with serve(contracts, host_type=SnapshotHost, payload_shape=shape, defect="double_decode") as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    assert result["status"] == "failed_assertion" and result["failure"]["code"] == "snapshot_payload"


@pytest.mark.parametrize("defect", ["missing_route", "operation_throw", "client", "undeclared"])
async def test_snapshot_binding_gaps_and_throws_remain_visible(specs, defect):
    case = snapshot_cases(specs)[0]
    contracts = Contracts()
    options = (
        {"missing_route": "/evaluate_flags/read"}
        if defect == "missing_route"
        else (
            {"defect": "operation_throw"}
            if defect == "operation_throw"
            else {"runtime": "client"} if defect == "client" else {}
        )
    )
    async with serve(contracts, host_type=SnapshotHost, **options) as (host, url):
        if defect == "undeclared":
            host.profile["sdk_capabilities"].remove("flag_snapshot_value_scalar")
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case.id])
    result = next(row["result"] for row in report["results"] if row["case_id"] == case.id)
    if defect == "client":
        assert result["status"] == "not_applicable" and not host.inputs
    elif defect == "missing_route":
        assert result["status"] == "unsupported_binding" and not result["executed"]
    elif defect == "operation_throw":
        assert result["status"] == "failed_assertion" and result["failure"]["code"] == "snapshot_thrown"
    else:
        assert result["status"] == "unsupported_binding" and result["failure"]["code"] == "snapshot_representation"


async def test_read_binding_forwards_only_json_arguments(specs):
    calls = []
    args = {
        "distinct_id": None,
        "options": {"person_properties": {"active": False, "attempt": 0}},
        "reads": [{"method": "only", "keys": [], "reads": []}],
    }

    async def call(route, arguments, **options):
        calls.append((route, arguments, options))
        return {"kind": "value", "value": {"results": []}}

    ctx = SimpleNamespace(call=call)
    step = SimpleNamespace(
        text="evaluate flags and read is called with JSON arguments:",
        argument={"docString": {"content": json.dumps(args), "mediaType": "application/json"}},
        source={"path": FEATURE, "line": 1},
    )
    handler, parameters = STEPS.bind(step)
    await handler(ctx, step, *parameters)
    assert calls == [("/evaluate_flags/read", args, {"check_result": False})]
    assert ctx.snapshot_read == (args["reads"], {"kind": "value", "value": {"results": []}})


@pytest.mark.parametrize("value", [0, 1, None, "true", {}, []])
def test_enablement_requires_boolean(value):
    with pytest.raises(BoundaryError, match="Public enablement"):
        semantic_read(
            SimpleNamespace(), {"method": "is_enabled", "key": "x"}, {"kind": "value", "value": value}, {"value": True}
        )


@pytest.mark.parametrize(
    "projection",
    [
        {"key": "wrong", "enabled": True, "variant": None},
        {"key": "x", "enabled": 1, "variant": None},
        {"key": "x", "enabled": True},
        {"key": "x", "enabled": True, "variant": 0},
        True,
    ],
)
def test_rich_public_projection_requires_declared_fields(projection):
    ctx = SimpleNamespace(profile={"sdk_capabilities": ["flag_snapshot_value_rich"]})
    with pytest.raises(BoundaryError, match="Rich public projection"):
        semantic_read(ctx, {"method": "get_flag", "key": "x"}, {"kind": "value", "value": projection}, {"value": True})


@pytest.mark.parametrize("shape", ["decoded", "serialized"])
@pytest.mark.parametrize(
    "actual,expected", [(False, 0), (0, False), (None, False), ([2, 1], [1, 2]), ({"a": 0}, {"a": False})]
)
def test_payload_semantics_are_recursively_type_strict(shape, actual, expected):
    ctx = SimpleNamespace(profile={"sdk_capabilities": ["flag_snapshot_payload_" + shape]})
    with pytest.raises(BoundaryError, match="Public payload semantics"):
        semantic_read(
            ctx,
            {"method": "get_flag_payload", "key": "x"},
            {"kind": "value", "value": json.dumps(actual) if shape == "serialized" else actual},
            {"payload": expected},
        )


@pytest.mark.parametrize("capabilities", [[], ["flag_snapshot_value_scalar", "flag_snapshot_value_rich"]])
def test_ambiguous_value_representation_is_a_binding_gap(capabilities):
    ctx = SimpleNamespace(profile={"sdk_capabilities": capabilities})
    with pytest.raises(BoundaryError) as error:
        semantic_read(ctx, {"method": "get_flag", "key": "x"}, {"kind": "value", "value": True}, {"value": True})
    assert error.value.kind == "unsupported_binding"
    ctx.profile["sdk_capabilities"].append("flag_snapshot_missing_undefined")
    with pytest.raises(BoundaryError) as missing_error:
        semantic_read(ctx, {"method": "get_flag", "key": "missing"}, {"kind": "undefined"}, {"missing": True})
    assert missing_error.value.kind == "unsupported_binding"


@pytest.mark.parametrize("sentinel", ["undefined", "null"])
def test_declared_missing_value_kind(sentinel):
    ctx = SimpleNamespace(
        profile={"sdk_capabilities": ["flag_snapshot_value_scalar", "flag_snapshot_missing_" + sentinel]}
    )
    outcome = {"kind": "undefined"} if sentinel == "undefined" else {"kind": "value", "value": None}
    semantic_read(ctx, {"method": "get_flag", "key": "missing"}, outcome, {"missing": True})


def test_serialized_empty_payload_missing_representation():
    ctx = SimpleNamespace(
        profile={"sdk_capabilities": ["flag_snapshot_payload_serialized", "flag_snapshot_payload_missing_empty_string"]}
    )
    semantic_read(
        ctx, {"method": "get_flag_payload", "key": "missing"}, {"kind": "value", "value": ""}, {"missing": True}
    )


@pytest.mark.parametrize(
    "outcome",
    [{"kind": "value"}, {"kind": "value", "value": None, "extra": True}, {"kind": "undefined", "value": None}],
)
def test_nested_read_outcome_schema_cannot_supply_implicit_null(outcome):
    ctx = SimpleNamespace(profile={"sdk_capabilities": ["flag_snapshot_payload_decoded"]})
    with pytest.raises(BoundaryError, match="Invalid native read outcome"):
        semantic_read(
            ctx,
            {"method": "only", "keys": ["x"], "reads": [{"method": "get_flag_payload", "key": "x"}]},
            {"kind": "value", "value": {"results": [outcome]}},
            {"results": [{"payload": None}]},
        )
    semantic_read(ctx, {"method": "get_flag_payload", "key": "x"}, {"kind": "value", "value": None}, {"payload": None})


@pytest.mark.parametrize(
    "capabilities",
    [
        ["flag_snapshot_payload_missing_empty_string"],
        ["flag_snapshot_payload_decoded", "flag_snapshot_payload_missing_empty_string"],
        [
            "flag_snapshot_payload_decoded",
            "flag_snapshot_payload_serialized",
            "flag_snapshot_payload_missing_empty_string",
        ],
    ],
)
def test_missing_payload_requires_coherent_representation(capabilities):
    ctx = SimpleNamespace(profile={"sdk_capabilities": capabilities})
    with pytest.raises(BoundaryError) as error:
        semantic_read(
            ctx, {"method": "get_flag_payload", "key": "missing"}, {"kind": "value", "value": ""}, {"missing": True}
        )
    assert error.value.kind == "unsupported_binding"
