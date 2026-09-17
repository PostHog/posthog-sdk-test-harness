"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.analytics_outcome_steps import absent_header
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.remote_flag_steps import count, event_count, event_property, field, query
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_analytics_retry import save_receipt
from tests.test_v2_gherkin import SPECS
from tests.v2_flush_host import serve
from tests.v2_remote_flags_host import RemoteFlagsHost

FEATURE = "migration/yaml-parity-v1/remote-flags-v1.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [case.id for case in CASES]


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


@pytest.mark.parametrize(
    "index,defect,code",
    [
        (0, "wrong_identity", "flag_request_field"),
        (0, "device_at_root", "flag_request_field"),
        (1, "wrong_version", "flag_request_query"),
        (2, "decide", "flag_request_count"),
        (3, "authorization", "header_present"),
        (4, "wrong_token", "flag_request_field"),
        (4, "token_shadows_alias", "flag_request_field"),
        (5, "wrong_group_properties", "flag_request_field"),
        (6, "omit_groups", "flag_request_field"),
        (7, "false_geoip_lost", "flag_request_field"),
        (8, "omit_geoip", "flag_request_field"),
        (9, "wrong_scope", "flag_request_field"),
        (10, "startup_flags", "flag_request_count"),
        (11, "startup_flags", "flag_request_count"),
        (11, "capture_flags", "flag_request_count"),
        (12, "unexpected_cache", "flag_request_count"),
        (13, "wrong_parse", "flag_value"),
        (14, "no_retry", "flag_request_count"),
        (15, "no_retry", "flag_request_count"),
        (16, "no_tracking", "flag_event_count"),
        (16, "tracking_key", "flag_event_property"),
        (16, "tracking_value", "flag_event_property"),
    ],
)
async def test_distinct_native_defects_fail_observed_layer(contracts, tmp_path, index, defect, code):
    async with serve(contracts, host_type=RemoteFlagsHost, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    save_receipt(tmp_path, report, diagnostics)
    row = report["results"][index]["result"]
    assert row["status"] == "failed_assertion", report
    assert row["failure"]["code"] == code, row
    assert row["failure"]["failed_step"] and row["failure"]["call_ids"]
    assert strict_exit_code(contracts, report) == 1


async def test_source_wire_scopes_alias_precedence_python_equality_and_named_event_first():
    def request(body, **kwargs):
        return SimpleNamespace(path="/flags", body_decompressed=json.dumps(body), query_params={"v": "2"}, **kwargs)

    recorded = [request({"api_key": "token", "groups": {}, "geoip_disable": 0}), request({"token": "wrong"})]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: recorded)))
    await field(ctx, None, "token", '"token"')
    await field(ctx, None, "geoip_disable", "false")
    await query(ctx, None, "v", "2")
    recorded[0].body_decompressed = '{"token": "earlier", "token": "token"}'
    await field(ctx, None, "token", '"token"')
    recorded[0].body_decompressed = "not-json"
    with pytest.raises(BoundaryError) as malformed:
        await field(ctx, None, "token", '"token"')
    assert malformed.value.kind == "failed_assertion"

    recorded[0].body_decompressed = '{"token": false, "api_key": "token"}'
    with pytest.raises(BoundaryError):
        await field(ctx, None, "token", '"token"')
    recorded[:] = [SimpleNamespace(path="/flags/extra"), SimpleNamespace(path="/decide")]
    await count(ctx, None, "1")
    recorded[:] = [
        SimpleNamespace(path="/batch", headers={}),
        SimpleNamespace(path="/flags", headers={"authorization": "later"}),
    ]
    await absent_header(ctx, None, "Authorization")
    recorded[0].headers["authorization"] = ""
    with pytest.raises(BoundaryError):
        await absent_header(ctx, None, "Authorization")
    recorded[:] = [
        SimpleNamespace(
            path="/batch",
            parsed_events=[{"event": "E", "properties": {"p": "wrong"}}, {"event": "E", "properties": {"p": "right"}}],
        )
    ]
    with pytest.raises(BoundaryError):
        await event_property(ctx, None, "E", "p", '"right"')
    recorded[0].path = "/other-received-path"
    await event_count(ctx, None, "2", "E")


async def test_complete_feature_through_public_http(contracts):
    async with serve(contracts, host_type=RemoteFlagsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], timeout_ms=60000)
    assert strict_exit_code(contracts, report) == 0, report
    assert all(row["result"]["status"] in ("passed", "not_selected") for row in report["results"])
    assert len(host.closed) == sum(row["result"]["executed"] for row in report["results"])
    assert len({d["mock_url"] for d in diagnostics["cases"]}) == len(diagnostics["cases"])
