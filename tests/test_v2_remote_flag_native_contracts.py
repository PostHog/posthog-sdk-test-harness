"""Native privacy defaults and failed legacy reads, without relaxing healthy values."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2 import remote_flag_steps as steps
from posthog_test_harness.v2.contracts import BoundaryError
from posthog_test_harness.v2.gherkin import compile_feature

FAILED_READERS = {"readers.dependency_error", "readers.error", "readers.error_with_value", "readers.v1_failed"}


def context(outcome=None, *, capabilities=(), wire=False, served=True, version="4"):
    fixture = json.dumps({"flags": {"checkout": {"failed": True}}})
    request = SimpleNamespace(
        path="/flags",
        body_decompressed=json.dumps({"geoip_disable": wire} if wire != "absent" else {}),
        query_params={"v": version},
        response_raw=True,
        response_body=fixture,
    )
    return SimpleNamespace(
        profile={"sdk_capabilities": list(capabilities)},
        server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: [request] if served else [])),
        flags_v4_body=fixture,
        remote_flag_arguments={"key": "checkout"},
        remote_flag_outcome=outcome,
    )


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("wire", [False, True, 0, 1, None, "false", {}, "absent"])
async def test_omitted_geoip_requires_the_declared_strict_boolean_default(disabled, wire):
    ctx = context(capabilities=["flags_geoip_default_disabled"] if disabled else [], wire=wire)
    if type(wire) is bool and wire is disabled:
        await steps.native_geoip_default(ctx, None)
    else:
        with pytest.raises(BoundaryError) as caught:
            await steps.native_geoip_default(ctx, None)
        assert caught.value.code == "flag_request_field"
        assert caught.value.details["expected"] is disabled


@pytest.mark.parametrize("value", [False, True])
async def test_explicit_geoip_override_remains_strict_in_both_default_profiles(value):
    for disabled in (False, True):
        ctx = context(capabilities=["flags_geoip_default_disabled"] if disabled else [], wire=value)
        await steps.field(ctx, None, "geoip_disable", json.dumps(value))
        ctx = context(capabilities=ctx.profile["sdk_capabilities"], wire=int(value))
        with pytest.raises(BoundaryError):
            await steps.field(ctx, None, "geoip_disable", json.dumps(value))


@pytest.mark.parametrize(
    "outcome",
    [
        {"kind": "undefined"},
        {"kind": "value", "value": None},
        {"kind": "value", "value": False},
    ],
)
async def test_failed_legacy_getter_accepts_only_documented_no_result_mappings(outcome):
    await steps.failed_result(context(outcome), None)


@pytest.mark.parametrize(
    "outcome",
    [
        {"kind": "void"},
        {"kind": "thrown", "error": {"name": "Error", "message": "boom"}},
        *({"kind": "value", "value": value} for value in [0, 1, True, "false", "", [], {}, {"kind": "undefined"}]),
    ],
)
async def test_failed_legacy_getter_rejects_real_or_arbitrary_values(outcome):
    with pytest.raises(BoundaryError) as caught:
        await steps.failed_result(context(outcome), None)
    assert caught.value.code == "flag_value"


@pytest.mark.parametrize("served,version", [(False, "4"), (True, "2")])
async def test_failed_legacy_getter_requires_served_v4_fixture(served, version):
    with pytest.raises(BoundaryError) as caught:
        await steps.failed_result(context({"kind": "undefined"}, served=served, version=version), None)
    assert caught.value.code == "flag_fixture_read"


def test_generated_native_failure_mapping_is_restricted_to_four_failed_readers():
    path = Path(__file__).resolve().parents[1] / "bin/update-flags-v4-features.py"
    spec = importlib.util.spec_from_file_location("native_flags_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    getter = module.rendered()["migration/yaml-parity-v1/remote-flags-v4-legacy-getter.feature"]
    cases = compile_feature(getter, "getter.feature", "revision")
    native = [c for c in cases if c.steps[-1].text == "the public flag getter should return false or no value"]
    assert {c.name.rsplit(" ", 1)[-1] for c in native} == FAILED_READERS
    assert len(cases) == 75
    for case in cases:
        if case.name.rsplit(" ", 1)[-1] not in FAILED_READERS:
            assert "return false or no value" not in case.steps[-1].text


@pytest.mark.parametrize(
    "outcome", [{"kind": "undefined"}, {"kind": "value", "value": None}, {"kind": "value", "value": 0}]
)
async def test_successful_disabled_decision_still_requires_exact_false(outcome):
    with pytest.raises(BoundaryError) as caught:
        await steps.exact_result(context(outcome), None, "false")
    assert caught.value.code == "flag_value"


@pytest.mark.parametrize("marker", [False, None, "true", 1])
async def test_native_failed_result_cannot_be_used_on_healthy_or_malformed_records(marker):
    ctx = context({"kind": "undefined"})
    ctx.flags_v4_body = json.dumps({"flags": {"checkout": {"failed": marker}}})
    with pytest.raises(BoundaryError) as caught:
        await steps.failed_result(ctx, None)
    assert caught.value.code == "invalid_step_data"


@pytest.mark.parametrize("disabled", [False, True])
async def test_geoip_defaults_and_both_explicit_overrides_through_rpc(specs, disabled):
    from posthog_test_harness.v2.contracts import Contracts
    from posthog_test_harness.v2.gherkin import load_cases
    from posthog_test_harness.v2.runner import run
    from tests.v2_flush_host import serve
    from tests.v2_remote_flags_host import RemoteFlagsEngine, RemoteFlagsHost

    class PrivacyEngine(RemoteFlagsEngine):
        geoip_default_disabled = disabled

        def __init__(self, storage, host, config, *args, **kwargs):
            assert "disable_geoip" not in config
            super().__init__(storage, host, config, *args, **kwargs)

    class PrivacyHost(RemoteFlagsHost):
        engine_type = PrivacyEngine

    feature = "migration/yaml-parity-v1/remote-flags-v1.feature"
    cases, _ = load_cases(specs, [feature])
    selected = [cases[i].id for i in (0, 7, 8)]
    contracts = Contracts()
    async with serve(contracts, host_type=PrivacyHost) as (host, url):
        if disabled:
            host.profile["sdk_capabilities"].append("flags_geoip_default_disabled")
        report, _ = await run(contracts, specs, [feature], url, host.profile["id"], case_ids=selected)
    assert [r["result"]["status"] for r in report["results"] if r["case_id"] in selected] == ["passed"] * 3


@pytest.mark.parametrize("reader", sorted(FAILED_READERS))
@pytest.mark.parametrize("kind", ["false", "null", "undefined", "zero", "thrown"])
async def test_authoritative_failed_reader_rows_through_rpc(specs, reader, kind):
    from posthog_test_harness.v2.contracts import Contracts
    from posthog_test_harness.v2.runner import run
    from tests.v2_flags_v4_host import UNDEFINED, FlagsV4Engine, FlagsV4Host
    from tests.v2_flush_host import serve

    class FailedEngine(FlagsV4Engine):
        async def get_feature_flag(self, args):
            await self.read(args)
            assert self.parsed_responses[-1]["flags"][args["key"]]["failed"] is True
            if kind == "thrown":
                raise RuntimeError("Native read failed")
            return {"false": False, "null": None, "undefined": UNDEFINED, "zero": 0}[kind]

    class FailedHost(FlagsV4Host):
        engine_type = FailedEngine

    contracts = Contracts()
    feature = "migration/yaml-parity-v1/remote-flags-v4-legacy-getter.feature"
    selected = f"{feature}::Legacy getter reads reader fixture {reader}"
    async with serve(contracts, host_type=FailedHost) as (host, url):
        report, _ = await run(contracts, specs, [feature], url, host.profile["id"], case_ids=[selected])
    row = next(r for r in report["results"] if r["case_id"] == selected)["result"]
    assert row["status"] == ("failed_assertion" if kind in ("zero", "thrown") else "passed"), row
    if kind in ("zero", "thrown"):
        assert row["failure"]["code"] == ("flag_getter_thrown" if kind == "thrown" else "flag_value")
