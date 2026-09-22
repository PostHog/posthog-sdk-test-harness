"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

import base64
import gzip
import hashlib
import json
import subprocess
import zlib
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.capture_amendment_steps import STEPS
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import observation, save_receipt
from tests.v2_analytics_wire_host import AnalyticsWireEngine, AnalyticsWireHost
from tests.v2_flush_host import serve
from tests.v2_legacy_capture_host import LegacyCaptureHost

FEATURE = "migration/yaml-parity-v1/capture-amendment-v1.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


ENCODINGS = ("gzip", "deflate", "br", "zstd")


CAPABILITIES = ["capture_v1", *["encoding_" + e for e in ENCODINGS]]


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
async def test_real_http_defects_reject_each_new_case(contracts, tmp_path, index, defect, code, specs, case_ids):
    host_type = LegacyCaptureHost if index == 11 else AnalyticsWireHost
    caps = ["capture_v0", "encoding_gzip"] if index == 11 else CAPABILITIES
    async with serve(contracts, host_type=host_type, sdk_capabilities=caps, defect=defect) as (host, url):
        report, diagnostics = await run(
            contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[index]]
        )
    save_receipt(tmp_path, report, diagnostics)
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
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


@pytest.mark.parametrize("legacy", [False, True])
async def test_complete_feature_through_public_http(contracts, legacy, specs):
    host_type = LegacyCaptureHost if legacy else AnalyticsWireHost
    caps = ["capture_v0", "encoding_gzip"] if legacy else CAPABILITIES
    async with serve(contracts, host_type=host_type, sdk_capabilities=caps) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], timeout_ms=60000)
    assert strict_exit_code(contracts, report) == 0, report
    assert sum(r["result"]["status"] == "passed" for r in report["results"]) == (1 if legacy else 11)


async def check(text, observed):
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    step = SimpleNamespace(text=text, argument={}, source={"path": FEATURE, "line": 1})
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)
