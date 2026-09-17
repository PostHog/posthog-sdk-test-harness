"""Pinned 33 legacy capture cases: 32 executable, one boolean-compression blocker."""

import hashlib
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.contracts import BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.legacy_capture_steps import STEPS
from posthog_test_harness.v2.migration import SUITE, migration_manifest, selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import table
from tests.test_v2_analytics_batching import actions, expected_calls
from tests.test_v2_analytics_batching import expected_assertion as batching_assertion
from tests.test_v2_analytics_retry import expected_assertion as retry_assertion
from tests.test_v2_analytics_retry import observation, save_receipt
from tests.test_v2_analytics_wire import LEGACY
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_flush_host import serve
from tests.v2_legacy_capture_host import LegacyCaptureHost

FEATURE = SUITE + "/capture-legacy.feature"
SOURCE = "contracts/capture_tests.yaml"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]
ALL_ORIGINS = [row for row in LEGACY if row["source"]["path"] == SOURCE]
ORIGINS = [row for i, row in enumerate(ALL_ORIGINS, 1) if i != 27]
BATCH_NUMBERS = {1, 5, 7, 28, 30}
EVENT_NUMBERS = {2, 6, 8}


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def source_number(case):
    return next(i for i, row in enumerate(ALL_ORIGINS, 1) if row["id"] == case.migration["legacy_id"])


def expected_assertion(action):
    name, params = action["action"], action.get("params", {})
    if name == "assert_event_field":
        return f'the first received event field "{params["field"]}" should equal "{params["expected"]}"'
    if name == "assert_event_property" and params.get("exists"):
        assert params == {"property": "$lib", "exists": True}
        return 'the first received event should contain property "$lib"'
    if name == "assert_token_present":
        return f'the first request should contain token "{params["expected"]}" at event token or body api_key or token'
    if name == "assert_token_present_client":
        return (
            f'an event in the first request should resolve token "{params["expected"]}" '
            "from event then property token or api_key"
        )
    if name == "assert_batch_format":
        assert params.get("has_batch_array") is True
        return "the first request body should contain a batch array" + (
            " and an api_key field" if params.get("has_api_key_field") else ""
        )
    if name == "assert_final_success":
        assert not params  # The pinned default is 200, not a newly supplied source input.
        return "at least one recorded response should have status 200"
    if name in {"assert_event_property", "assert_event_has_field", "assert_all_uuids_unique", "assert_different_uuids"}:
        return batching_assertion(action)[0]
    return retry_assertion(action)


def translated_calls(origin):
    calls = deepcopy(expected_calls(origin))
    for route, args in calls:
        if route == "/setup":
            args["project_token"] = args["config"].pop("api_key", "phc_test_key")
    return calls


def test_exact_source_partition_inputs_defaults_assertions_and_grouped_requirements():
    assert len(ALL_ORIGINS) == 33 and len(CASES) == len(ORIGINS) == 32
    for case, origin in zip(CASES, ORIGINS):
        row, number = case.migration, source_number(case)
        variant = "batch" if number in BATCH_NUMBERS else "event" if number in EVENT_NUMBERS else None
        assert row["legacy_id"] == origin["id"] and row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert row["candidate_routes"] == ["/capture"]
        assert row["sdk_capabilities"] == ["capture_v0"] + (["capture_v0_" + variant] if variant else [])
        assert row["native_sdk_evidence"] == []
        assert case.id == "migration:yaml-parity-v1:capture:" + origin["name"]
        assert {"@both", "@api_capture_v0"} <= set(case.tags)
        assert len(case.steps) == len(actions(origin)) + 2
        for step, action in zip(case.steps[2:], actions(origin)):
            name, params = action["action"], action.get("params", {})
            if name == "configure_mock_responses":
                assert step.text == "the mock serves these ordered analytics responses:"
                actual = table(step, dict.fromkeys(("status", "headers", "body", "event_results"), "json"))
                expected = [
                    {
                        "status": r.get("status_code", 200),
                        "headers": r.get("headers", {}),
                        "body": r.get("body"),
                        "event_results": None,
                    }
                    for r in params["responses"]
                ]
                assert json_equal(actual, expected)
            elif name == "init":
                assert set(params) <= {"api_key", "flush_at", "max_retries"}
                suffix = "no additional configuration"
                if "flush_at" in params:
                    suffix = f'flush threshold {params["flush_at"]}'
                if "max_retries" in params:
                    suffix = f'maximum retries {params["max_retries"]}'
                assert (
                    step.text
                    == f'the SDK is initialized with token "{params.get("api_key", "phc_test_key")}" and ' + suffix
                )
            elif name in ("capture", "capture_multiple"):
                assert step.text == (
                    "capture is called with JSON arguments:"
                    if name == "capture"
                    else f'capture is called sequentially {params["count"]} times '
                    "with zero-based top-level index substitution:"
                )
                assert json_equal(json_arguments(step), params if name == "capture" else params["params"])
            elif name == "flush":
                assert step.text == "pending captures are flushed"
            elif name == "wait":
                assert step.text == f'{params["duration_ms"]} milliseconds elapse without a public SDK call'
            else:
                assert step.text == expected_assertion(action)
    manifest = migration_manifest(SPECS)
    source = next(row for row in manifest["legacy_sources"] if row["path"] == SOURCE)
    assert source["sha256"] == hashlib.sha256((SPECS.parent / "harness" / SOURCE).read_bytes()).hexdigest()
    assert all(row["status"] == "harness_ready" for row in discover(SPECS, [FEATURE])["cases"])


def test_historical_compression_blocker_preserves_source_traceability(contracts):
    blockers = json.loads((SPECS / SUITE / "blocked-cases.json").read_text())
    assert len(blockers) == 12
    row = next(row for row in blockers if row["legacy_source"]["path"] == SOURCE)
    origin = ALL_ORIGINS[26]
    assert origin["name"] == "sends_gzip_when_enabled"
    assert row["legacy_id"] == origin["id"] and row["legacy_source"] == origin["source"]
    assert row["legacy_filters"] == origin["capability_filters"]
    assert json_equal(row["source_inputs_and_ordered_assertions"], actions(origin))
    assert row["sdk_capabilities"] == ["capture_v0", "encoding_gzip"]
    assert row["unrepresentable_fields"] == ["SetupConfig.enable_compression"]
    assert row["status"] == "blocked_contract" and row["translation"] == "not_translated"
    assert row["execution_evidence"] == row["native_sdk_evidence"] == []
    assert row["id"] not in IDS
    config = contracts.schemas["catalog"]["definitions"]["SetupConfig"]["properties"]
    assert "enable_compression" not in config
    assert config["compression"] == {"$ref": "#/definitions/Compression"}
    assert set(contracts.schemas["catalog"]["definitions"]["Compression"]["enum"]) == {
        "none",
        "gzip",
        "deflate",
        "br",
        "zstd",
    }
    assert {row["legacy_id"]} | {case.migration["legacy_id"] for case in CASES} == {o["id"] for o in ALL_ORIGINS}


async def test_all_32_execute_real_http_with_exact_calls_and_original_windows(contracts, tmp_path):
    executed, public_calls, started = set(), 0, time.monotonic()
    for variant, runtime in (("batch", "mobile"), ("event", "server")):
        selected = [
            case
            for case in CASES
            if variant == "batch"
            and source_number(case) not in EVENT_NUMBERS
            or variant == "event"
            and source_number(case) in EVENT_NUMBERS
        ]
        async with serve(contracts, host_type=LegacyCaptureHost, wire_variant=variant, runtime=runtime) as (host, url):
            report, diagnostics = await run(
                contracts,
                SPECS,
                [FEATURE],
                url,
                host.profile["id"],
                case_ids=[case.id for case in selected],
                timeout_ms=60000,
            )
        receipt = tmp_path / variant
        receipt.mkdir()
        save_receipt(receipt, report, diagnostics)
        assert strict_exit_code(contracts, report) == 0, report
        assert {r["case_id"] for r in report["results"] if r["result"]["status"] == "passed"} == {
            c.id for c in selected
        }
        executed.update(c.id for c in selected)
        public_calls += len(report["calls"])
        actual = []
        for row in host.inputs:
            call = deepcopy(row["invoke"])
            if call["route"] == "/setup":
                assert call["args"]["config"].pop("host").startswith("http://127.0.0.1:")
            actual.append([call["route"], call["args"]])
        origins = [o for o in ORIGINS if o["id"] in {c.migration["legacy_id"] for c in selected}]
        assert json_equal(actual, [list(call) for origin in origins for call in translated_calls(origin)])
        assert len(host.closed) == len(selected) and all(f.closed and f.engine is None for f in host.fixtures.values())
        for case, observed in zip(selected, diagnostics["cases"]):
            wire = observed["wire_requests"]
            assert all(r["path"] == ("/batch" if variant == "batch" else "/e/") for r in wire)
            origin = next(o for o in origins if o["id"] == case.migration["legacy_id"])
            configured = next(
                (a["params"]["responses"] for a in actions(origin) if a["action"] == "configure_mock_responses"), []
            )
            for request, response in zip(wire, configured):
                assert request["response_status"] == response.get("status_code", 200)
                if response.get("body"):
                    assert request["response_body"] == response["body"]
                assert response.get("headers", {}).items() <= request["response_headers"].items()
            if origin["name"] == "max_retries_respected":
                assert len(wire) == 4
            if origin["name"] == "respects_retry_after_header":
                assert wire[1]["timestamp_ms"] - wire[0]["timestamp_ms"] >= 2500
    waits = sum(a.get("params", {}).get("duration_ms", 0) for o in ORIGINS for a in actions(o) if a["action"] == "wait")
    assert waits == 88000 and time.monotonic() - started >= waits / 1000
    assert executed == set(IDS) and public_calls == sum(len(translated_calls(o)) for o in ORIGINS)


@pytest.mark.parametrize("variant,runtime", [("batch", "mobile"), ("event", "server")])
async def test_automatic_variant_selection_executes_every_grouped_obligation(contracts, variant, runtime):
    numbers = BATCH_NUMBERS if variant == "batch" else EVENT_NUMBERS
    selected = [c for c in CASES if source_number(c) in numbers]
    async with serve(contracts, host_type=LegacyCaptureHost, wire_variant=variant, runtime=runtime) as (host, url):
        host.profile["products"] = ["flags"]
        host.profile["protocol"] = "analytics_v1"  # Metadata cannot silently choose the engine's wire contract.
        for case in CASES:
            assert selection(case, host.profile, host.routes)["selected"] == (
                source_number(case) not in (EVENT_NUMBERS if variant == "batch" else BATCH_NUMBERS)
            )
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[c.id for c in selected])
    assert strict_exit_code(contracts, report) == 0
    assert sum(r["result"]["status"] == "passed" for r in report["results"]) == len(numbers)


@pytest.mark.parametrize("runtime", ["server", "browser", "mobile", "edge"])
def test_shape_claims_are_independent_of_role_protocol_identity_products_and_fixture(runtime):
    for variant, numbers in (("batch", BATCH_NUMBERS), ("event", EVENT_NUMBERS)):
        profile = {
            "runtime": {"family": runtime},
            "protocol": "analytics_v1",
            "identity": "request_scoped",
            "products": ["flags"],
            "fixture_capabilities": ["capture_v0_" + variant],
            "sdk_capabilities": ["capture_v0"],
        }
        for case in CASES:
            number = source_number(case)
            base = selection(case, profile, ["/capture"])
            assert base["selected"] == (number not in BATCH_NUMBERS | EVENT_NUMBERS)
            if number in numbers:
                assert base["missing_sdk_capabilities"] == ["capture_v0_" + variant]
                assert "capture_v0_" + variant in base["reason"]
                declared = {**profile, "sdk_capabilities": ["capture_v0", "capture_v0_" + variant]}
                assert selection(case, declared, ["/capture"])["selected"]
                assert selection(case, declared, [])["selected"]
                assert not selection(case, {**profile, "sdk_capabilities": ["capture_v0_" + variant]}, ["/capture"])[
                    "selected"
                ]
                assert selection(case, profile, [], explicit=True)["selected"]


@pytest.mark.parametrize(
    "defect,number,variant,code",
    [
        ("wrong_root_identity", 1, "batch", "event_field"),
        ("wrong_property_identity", 2, "event", "event_property"),
        ("missing_uuid", 3, "batch", "event_field_missing"),
        ("invalid_uuid", 3, "batch", "event_uuid"),
        ("omit_lib", 4, "batch", "event_property_missing"),
        ("missing_token", 7, "batch", "request_token"),
        ("missing_token", 8, "event", "event_token"),
        ("shadow_event_token", 8, "event", "event_token"),
        ("property_value:0:custom_number:43", 9, "batch", "event_property"),
        ("omit_event:timestamp", 10, "batch", "event_field_missing"),
        ("offset_timestamp", 11, "batch", "event_timestamp"),
        ("rewrite_property", 11, "batch", "event_property"),
        ("no_retry", 18, "batch", "request_count"),
        ("ignore_retry_after", 15, "batch", "retry_delay"),
        ("no_backoff", 16, "batch", "retry_delay"),
        ("no_retry", 20, "batch", "request_count"),
        ("duplicate_uuids", 21, "batch", "uuid_unique"),
        ("retry_uuid", 22, "batch", "retry_event_uuid"),
        ("retry_timestamp", 24, "batch", "retry_event_timestamp"),
        ("duplicate_uuids", 25, "batch", "batch_duplicate_uuid"),
        ("duplicate_uuids", 26, "batch", "uuid_pair"),
        ("omit_body:api_key", 28, "batch", "legacy_batch"),
        ("object_batch", 30, "batch", "legacy_batch"),
        ("startup_capture", 29, "batch", "request_count"),
        ("duplicate_request", 31, "batch", "request_count"),
    ],
)
async def test_distinct_legacy_defects_fail_real_http_with_source_attribution(
    contracts, tmp_path, defect, number, variant, code
):
    case = next(c for c in CASES if source_number(c) == number)
    async with serve(contracts, host_type=LegacyCaptureHost, wire_variant=variant, defect=defect) as (host, url):
        report, diagnostics = await run(
            contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[case.id], timeout_ms=60000
        )
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][IDS.index(case.id)]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) == len(translated_calls(ALL_ORIGINS[number - 1]))
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "options,explicit,status,code",
    [
        ({"sdk_capabilities": []}, False, "not_selected", None),
        ({"sdk_capabilities": ["capture_v0"]}, True, "unsupported_binding", "sdk_capability_unavailable"),
        ({"missing_route": "/capture"}, False, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/setup"}, True, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/flush"}, True, "unsupported_binding", "missing_operation"),
        ({"sdk_capabilities": [], "missing_route": "/capture"}, True, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, True, "blocked_fixture", "fixture_unavailable"),
    ],
)
async def test_missing_binding_claims_and_fixtures_are_visible_before_allocation(
    contracts, options, explicit, status, code
):
    async with serve(contracts, host_type=LegacyCaptureHost, **options) as (host, url):
        report, _ = await run(
            contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]] if explicit else []
        )
    result = report["results"][0]["result"]
    assert result["status"] == status
    if code:
        assert result["failure"]["code"] == code
    assert not host.fixtures and strict_exit_code(contracts, report) == 1


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


BATCH_TOKEN = 'the first request should contain token "key" at event token or body api_key or token'
EVENT_TOKEN = 'an event in the first request should resolve token "key" from event then property token or api_key'


async def test_token_locations_first_request_all_events_and_first_truthy_precedence():
    for event, body in [({"token": "key"}, "not-json"), ({}, '{"api_key":"key"}'), ({}, '{"token":"key"}')]:
        await check(BATCH_TOKEN, [observation([{}, event], body_decompressed=body), observation()])
    for event, body in [({"api_key": "key"}, "{}"), ({"properties": {"token": "key"}}, "{}"), ({}, "not-json")]:
        with pytest.raises(BoundaryError):
            await check(BATCH_TOKEN, [observation([event], body_decompressed=body), observation([{"token": "key"}])])
    for event in [
        {"token": "key"},
        {"api_key": "key"},
        {"properties": {"token": "key"}},
        {"properties": {"api_key": "key"}},
        {"token": "", "api_key": "key"},
    ]:
        await check(EVENT_TOKEN, [observation([{}, event], body_decompressed="{}")])
    for event in [
        {},
        {"token": "wrong", "properties": {"token": "key"}},
        {"api_key": "wrong", "properties": {"api_key": "key"}},
        {"properties": {"token": "wrong", "api_key": "key"}},
    ]:
        with pytest.raises(BoundaryError):
            await check(
                EVENT_TOKEN,
                [observation([event], body_decompressed='{"api_key":"key"}'), observation([{"token": "key"}])],
            )
    for text in (BATCH_TOKEN, EVENT_TOKEN):
        with pytest.raises(BoundaryError):
            await check(text, [])


async def test_presence_identity_batch_and_uuid_assertions_retain_source_weaknesses():
    first = observation(
        [{"uuid": None, "timestamp": None, "properties": {"$lib": None}}],
        body_decompressed='{"api_key":null,"batch":[]}',
    )
    for text in [
        'the first received event should contain root field "uuid"',
        'the first received event should contain root field "timestamp"',
        'the first received event should contain property "$lib"',
        "the first request body should contain a batch array and an api_key field",
    ]:
        await check(text, [first, observation()])
    first.body_decompressed = '{"batch":[]}'
    await check("the first request body should contain a batch array", [first])
    with pytest.raises(BoundaryError):
        await check("the first request body should contain a batch array and an api_key field", [first])
    first.parsed_events = [{"distinct_id": "test_user", "properties": {"distinct_id": "test_user"}}, {}]
    await check('the first received event field "distinct_id" should equal "test_user"', [first])
    await check('the first received event property "distinct_id" should equal JSON "test_user"', [first])
    for text in [
        "all present UUIDs across received requests should be unique",
        'the present event "uuid" lists in requests zero and one should be identical',
        'the present event "timestamp" lists in requests zero and one should be identical',
        "every received batch should have no duplicate nonempty UUIDs",
    ]:
        await check(text, [first, observation([{}]), observation([{"uuid": "later"}])])
    first.parsed_events = [{"uuid": "0198c0de-0000-4000-8000-000000000abc"}, {"uuid": "invalid"}]
    await check("the first received event UUID should be valid", [first])
    first.parsed_events = [{"properties": {"custom_number": True}}]
    await check('the first received event property "custom_number" should equal JSON 1', [first])


async def test_legacy_counts_include_flags_and_first_delay_is_not_exponential_proof():
    observed = [observation(), observation(), observation()]
    observed[0].path = "/flags"
    observed[0].response_status = 200
    observed[1].timestamp_ms, observed[2].timestamp_ms = 100, 101
    await check("exactly 3 capture request should have been received", observed)
    await check("at least one recorded response should have status 200", observed)
    await check("the first inter-request delay should be at least 100 milliseconds", observed)
    with pytest.raises(BoundaryError):
        await check("exactly 2 capture request should have been received", observed)


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("missing_token", 1)])
async def test_legacy_cli_outside_checkout_failure_and_next_case_isolation(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=LegacyCaptureHost, runtime="mobile", defect=defect) as (host, url):
        code, report, _, output = await cli_run(
            tmp_path,
            url,
            "--feature",
            FEATURE,
            "--profile",
            host.profile["id"],
            "--case-id",
            IDS[6],
            "--case-id",
            IDS[27],
        )
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert report["results"][27]["result"]["status"] == "passed"
    assert len(host.closed) == 2
