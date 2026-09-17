"""Capture-amendment-v1: exact source mapping, effective contract and real HTTP encodings."""

import base64
import gzip
import hashlib
import json
import shutil
import subprocess
import zlib
from copy import deepcopy
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.capture_amendment_steps import STEPS
from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import BASE_CATALOG_HASH, BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, migration_manifest, selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_batching import actions, expected_calls
from tests.test_v2_analytics_retry import observation, save_receipt
from tests.test_v2_analytics_wire import LEGACY
from tests.test_v2_boundary import PROFILE
from tests.test_v2_boundary import serve as boundary_serve
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_analytics_wire_host import AnalyticsWireEngine, AnalyticsWireHost
from tests.v2_flush_host import serve
from tests.v2_legacy_capture_host import LegacyCaptureHost

FEATURE = SUITE + "/capture-amendment-v1.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [c.id for c in CASES]
HISTORICAL = json.loads((SPECS / SUITE / "blocked-cases.json").read_text())
ORIGINS = [next(o for o in LEGACY if o["id"] == row["legacy_id"]) for row in HISTORICAL]
ENCODINGS = ("gzip", "deflate", "br", "zstd")
CAPABILITIES = ["capture_v1", *["encoding_" + e for e in ENCODINGS]]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def translated_calls(index):
    calls = deepcopy(expected_calls(ORIGINS[index]))
    for route, args in calls:
        if route == "/setup" and "enable_compression" in args["config"]:
            assert args["config"].pop("enable_compression") is True
            args["config"]["compression"] = CASES[index].migration["argument_translations"][0]["target_value"]
    return [list(call) for call in calls]


def test_all_12_source_ids_typed_inputs_ordered_assertions_and_approved_mapping():
    assert len(CASES) == len(HISTORICAL) == 12
    amendment = json.loads((SPECS / SUITE / "approved-amendments.json").read_text())
    assert [r["id"] for r in amendment["resolutions"]] == IDS
    metadata = migration_manifest(SPECS)["approved_amendments"]
    assert hashlib.sha256((SPECS / metadata["path"]).read_bytes()).hexdigest() == metadata["sha256"]
    for case, origin, historical in zip(CASES, ORIGINS, HISTORICAL):
        row = case.migration
        assert row["legacy_id"] == origin["id"] == historical["legacy_id"]
        assert row["legacy_source"] == origin["source"]
        assert row["legacy_filters"] == origin["capability_filters"]
        assert row["sdk_capabilities"] == historical["sdk_capabilities"]
        assert row["amendment"] == "capture-amendment-v1" and row["native_sdk_evidence"] == []
        assert json_equal(actions(origin), historical["source_inputs_and_ordered_assertions"])
        assert len(case.steps) == len(actions(origin)) + 2
        for step, action in zip(case.steps[2:], actions(origin)):
            name, params = action["action"], action.get("params", {})
            if name == "init":
                if "enable_compression" in params:
                    (mapping,) = row["argument_translations"]
                    assert mapping["source"] == "init.enable_compression" and mapping["value"] is True
                    assert mapping["target"] == "/setup.config.compression"
                    assert "encoding_" + mapping["target_value"] in row["sdk_capabilities"]
                    expected = (
                        f'the SDK is initialized with token "phc_test_key" and compression "{mapping["target_value"]}"'
                    )
                    if "flush_at" in params:
                        expected += f' and flush threshold {params["flush_at"]}'
                elif "disable_geoip" in params:
                    expected = 'the SDK is initialized with token "phc_test_key", flush threshold 1, and GeoIP disabled'
                else:
                    expected = (
                        f'the SDK is initialized with token "phc_test_key" and flush threshold {params["flush_at"]}'
                    )
            elif name in ("capture", "capture_multiple"):
                expected = (
                    "capture is called with JSON arguments:"
                    if name == "capture"
                    else (
                        f'capture is called sequentially {params["count"]} times '
                        "with zero-based top-level index substitution:"
                    )
                )
                assert json_equal(json_arguments(step), params if name == "capture" else params["params"])
            elif name == "flush":
                expected = "pending captures are flushed"
            elif name == "assert_request_has_header":
                expected = f'a received request header "{params["header"]}" should equal "{params["expected"]}"'
            elif name == "assert_compressed_body_decompressible":
                expected = "the first encoded request should decompress to parseable events"
            elif name == "assert_event_option":
                expected = f'the first received event option "{params["option"]}" should equal JSON ' + json.dumps(
                    params["expected"]
                )
            elif name == "assert_event_property":
                expected = f'the first received event property "{params["property"]}" should equal JSON ' + json.dumps(
                    params["expected"]
                )
            elif name == "assert_events_in_batch_count":
                expected = f'the first request should contain exactly {params["expected"]} parsed events'
            else:
                assert name == "assert_request_count"
                expected = f'exactly {params["expected"]} capture request should have been received'
            assert step.text == expected


@pytest.mark.parametrize("legacy", [False, True])
async def test_all_12_execute_real_http_with_exact_amended_inputs(contracts, tmp_path, legacy):
    indices = [11] if legacy else list(range(11))
    options = (
        {"host_type": LegacyCaptureHost, "sdk_capabilities": ["capture_v0", "encoding_gzip"]}
        if legacy
        else {
            "host_type": AnalyticsWireHost,
            "sdk_capabilities": CAPABILITIES,
        }
    )
    async with serve(contracts, **options) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    save_receipt(tmp_path, report, diagnostics)
    assert strict_exit_code(contracts, report) == 0, report
    assert [i for i, r in enumerate(report["results"]) if r["result"]["status"] == "passed"] == indices
    actual = []
    for input in host.inputs:
        call = deepcopy(input["invoke"])
        if call["route"] == "/setup":
            assert call["args"]["config"].pop("host").startswith("http://127.0.0.1:")
        contracts.validate(
            contracts.operations[call["route"]]["arguments_schema"].split("/")[-1], call["args"], "catalog"
        )
        actual.append([call["route"], call["args"]])
    assert json_equal(actual, [call for i in indices for call in translated_calls(i)])
    (tmp_path / "inputs.json").write_text(json.dumps(actual, indent=2) + "\n")
    assert len(host.closed) == len(indices)
    for i in indices:
        assert report["results"][i]["result"]["executed"] is True


@pytest.mark.parametrize(
    "index,defect,code",
    [
        *[(i, "omit_compression", "request_header") for i in (0, 1, 2, 3, 11)],
        (4, "omit_compression", "compression_encoding"),
        (4, "invalid_compressed_body", "compression_body"),
        (4, "empty_compressed_events", "compression_events"),
        *[(i, "omit_options", "event_option") for i in (5, 6, 7, 8, 9)],
        (7, "drop_false_options", "event_option"),
        (5, "options_in_properties", "event_option"),
        (10, "omit_geoip", "event_property"),
    ],
)
async def test_real_http_defects_reject_each_new_case(contracts, tmp_path, index, defect, code):
    host_type = LegacyCaptureHost if index == 11 else AnalyticsWireHost
    caps = ["capture_v0", "encoding_gzip"] if index == 11 else CAPABILITIES
    async with serve(contracts, host_type=host_type, sdk_capabilities=caps, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(result["failure"]["call_ids"]) == len(translated_calls(index))
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("encoding", ENCODINGS)
async def test_controlled_engine_sends_real_compressed_http_bytes(encoding, tmp_path):
    server = CaseServer()
    try:
        engine = AnalyticsWireEngine({}, server.url, {"compression": encoding}, "phc_test_key", "analytics_v1", None)
        await engine.capture({"event": "compressed", "distinct_id": "user", "properties": {"value": False}})
        await engine.flush()
        (request,) = server.state.get_requests()
        assert request.headers["content-encoding"] == encoding
        raw = request.body_raw
        if encoding == "gzip":
            decoded = gzip.decompress(raw)
        elif encoding == "deflate":
            decoded = zlib.decompress(raw)
        else:
            decoded = subprocess.run(
                ["brotli" if encoding == "br" else "zstd", "-d", "-c"],
                input=raw,
                capture_output=True,
                check=True,
                timeout=10,
            ).stdout
        body = json.loads(decoded)
        assert body["batch"][0]["event"] == "compressed" and body["batch"][0]["properties"] == {"value": False}
        assert raw != decoded
        (tmp_path / "encoding-receipt.json").write_text(
            json.dumps(
                {
                    "encoding": encoding,
                    "raw_base64": base64.b64encode(raw).decode(),
                    "decoded": body,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "evidence": "supplemental controlled engine roundtrip, not an added source scenario assertion",
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        server.close()


@pytest.mark.parametrize("runtime", ["server", "browser", "mobile", "edge"])
def test_multiple_encoding_declarations_and_selection_are_independent_of_role(runtime):
    for i, encoding in enumerate(ENCODINGS):
        profile = {
            "runtime": {"family": runtime},
            "sdk_capabilities": ["capture_v1"],
            "fixture_capabilities": ["encoding_" + encoding],
        }
        assert not selection(CASES[i], profile, ["/capture"])["selected"]
        profile["sdk_capabilities"] = CAPABILITIES
        assert selection(CASES[i], profile, ["/capture"])["selected"]
        assert selection(CASES[i], profile, [])["missing_routes"] == ["/capture"]
        profile["sdk_capabilities"] = ["encoding_" + encoding]
        assert not selection(CASES[i], profile, ["/capture"])["selected"]


@pytest.mark.parametrize("disable_geoip", [None, False, True])
async def test_init_geoip_and_event_options_preserve_omitted_false_empty_native_inputs(disable_geoip):
    server = CaseServer()
    try:
        config = {} if disable_geoip is None else {"disable_geoip": disable_geoip}
        engine = AnalyticsWireEngine({}, server.url, config, "phc_test_key", "analytics_v1", None)
        args = {"event": "first", "distinct_id": "user"}
        await engine.capture(args)
        await engine.capture(
            {
                **args,
                "event": "second",
                "options": {
                    "cookieless_mode": False,
                    "disable_skew_correction": False,
                    "process_person_profile": False,
                    "product_tour_id": "",
                },
            }
        )
        await engine.capture({**args, "event": "third", "options": {}})
        await engine.flush()
        (request,) = server.state.get_requests()
        first, second, third = request.parsed_events
        assert "options" not in first and third["options"] == {}
        assert second["options"] == {
            "cookieless_mode": False,
            "disable_skew_correction": False,
            "process_person_profile": False,
            "product_tour_id": "",
        }
        for event in request.parsed_events:
            assert "options" not in event["properties"]
            if disable_geoip is None:
                assert "$geoip_disable" not in event["properties"]
            else:
                assert event["properties"]["$geoip_disable"] is disable_geoip
        assert "content-encoding" not in request.headers
    finally:
        server.close()


async def test_new_typed_and_negative_arguments_reach_transport_without_coercion(contracts):
    calls = [
        ("/capture", {"event": "x"}),
        ("/capture", {"event": "x", "options": {"process_person_profile": False}}),
        ("/setup", {"project_token": "test", "config": {"disable_geoip": False}}),
        ("/capture", {"event": "x", "options": {"cookieless_mode": "false"}}),
        ("/setup", {"project_token": "test", "config": {"disable_geoip": None}}),
    ]
    async with boundary_serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                for index, (route, args) in enumerate(calls):
                    name = contracts.operations[route]["arguments_schema"].split("/")[-1]
                    if index < 3:
                        contracts.validate(name, args, "catalog")
                    else:
                        with pytest.raises(BoundaryError):
                            contracts.validate(name, args, "catalog")
                    await fixture.invoke(str(index), route, args)
                    assert json_equal(host.inputs[-1]["invoke"]["args"], args)


async def test_base_only_negotiation_is_catalog_mismatch_before_allocation(contracts):
    async with serve(contracts, host_type=AnalyticsWireHost) as (host, url):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url + "/v2/negotiate",
                json={"contract_version": "2.0.0", "catalog_sha256": BASE_CATALOG_HASH, "transport": "http-json-v2"},
            ) as response:
                assert (await response.json())["code"] == "catalog_mismatch"
        assert not host.fixtures


@pytest.mark.parametrize("accepted", [True, False])
async def test_client_rejects_base_only_adapter_identity(contracts, accepted):
    paths = []

    async def handle(request):
        paths.append(request.path)
        data = await request.json()
        assert data["catalog_sha256"] == contracts.catalog_hash != BASE_CATALOG_HASH
        response = {"kind": "rejected", "code": "catalog_mismatch", "message": "Base catalog only"}
        if accepted:
            response = {
                "kind": "accepted",
                **data,
                "catalog_sha256": BASE_CATALOG_HASH,
                "session_id": "base-only",
                "adapter": {"name": "base-only", "version": "1"},
                "profiles": [PROFILE],
                "supported_routes": ["/capture"],
                "max_timeout_ms": 5000,
            }
        return web.json_response(response)

    app = web.Application()
    app.router.add_post("/v2/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        with pytest.raises(BoundaryError) as error:
            async with Client(url, contracts):
                pytest.fail("Base-only adapter accepted")
        assert error.value.code == "catalog_mismatch"
        assert paths == ["/v2/negotiate"]
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("corruption", ["amendment", "manifest", "schema"])
def test_consumer_verifies_effective_identity_and_pinned_amendment(contracts, tmp_path, corruption):
    for directory in ("inputs", "generated"):
        shutil.copytree(CONTRACT_PATH / directory, tmp_path / directory)
    if corruption == "amendment":
        with (tmp_path / "inputs/capture-amendment-v1.ts").open("a") as file:
            file.write("\n// changed\n")
    else:
        file = tmp_path / "generated" / ("operations.json" if corruption == "manifest" else "protocol.schema.json")
        data = json.loads(file.read_text())
        if corruption == "manifest":
            data["catalog_sha256"] = BASE_CATALOG_HASH
        else:
            data["definitions"]["CatalogHash"]["const"] = BASE_CATALOG_HASH
        file.write_text(json.dumps(data))
    with pytest.raises(BoundaryError):
        Contracts(tmp_path)


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


async def test_original_wire_scopes_not_strengthened_by_compression_or_options():
    first = observation([{"options": {"cookieless_mode": 1}}, {"options": {"cookieless_mode": False}}])
    later = observation([{}])
    later.headers = {"Content-Encoding": "gzip"}
    await check('a received request header "Content-Encoding" should equal "gzip"', [first, later])
    await check('the first received event option "cookieless_mode" should equal JSON true', [first, later])
    with pytest.raises(BoundaryError):
        await check("the first encoded request should decompress to parseable events", [first, later])
    first.headers = {"content-encoding": "gzip"}
    first.body_decompressed = '{"batch":[{}]}'
    await check("the first encoded request should decompress to parseable events", [first, later])
    first.parsed_events[0] = {"properties": {"options": {"cookieless_mode": True}}}
    with pytest.raises(BoundaryError):
        await check('the first received event option "cookieless_mode" should equal JSON true', [first, later])


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("drop_false_options", 1)])
async def test_amendment_cli_outside_checkout_with_next_case_isolation(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect, runtime="browser") as (host, url):
        code, report, _, output = await cli_run(
            tmp_path,
            url,
            "--feature",
            FEATURE,
            "--profile",
            host.profile["id"],
            "--case-id",
            IDS[7],
            "--case-id",
            IDS[10],
        )
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert report["results"][10]["result"]["status"] == "passed"
    assert len(host.closed) == 2


@pytest.mark.parametrize(
    "options,status,code",
    [
        ({"sdk_capabilities": ["capture_v1"]}, "unsupported_binding", "sdk_capability_unavailable"),
        ({"missing_route": "/capture"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/setup"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/flush"}, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, "blocked_fixture", "fixture_unavailable"),
    ],
)
async def test_amendment_prerequisite_gaps_remain_attributed(contracts, options, status, code):
    options = {"sdk_capabilities": CAPABILITIES, **options}
    async with serve(contracts, host_type=AnalyticsWireHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[0]])
    result = report["results"][0]["result"]
    assert result["status"] == status and result["failure"]["code"] == code
    assert not host.fixtures and strict_exit_code(contracts, report) == 1
