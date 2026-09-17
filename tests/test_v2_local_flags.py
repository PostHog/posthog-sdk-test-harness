"""Native flag-state preparation, real observations and local/remote routing."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.cached_flag_steps import STEPS as PREVIOUS_STEPS
from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.discovery import discover, feature_paths
from posthog_test_harness.v2.fixtures import CaseServer, FlushControls
from posthog_test_harness.v2.flag_fixtures import FlagStateControls
from posthog_test_harness.v2.local_flag_steps import STEPS as LOCAL_STEPS
from posthog_test_harness.v2.local_flag_steps import constant_definition
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, PIN, SPECS
from tests.v2_flush_host import serve
from tests.v2_local_flags_host import LocalFlagsEngine, LocalFlagsHost


def identity(feature, line):
    return f"gherkin:{PIN[:7]}:acceptance/public/{feature}.feature:L{line}"


IDS = [identity("evaluate-flags", line) for line in (115, 145, 407, 421, 435, 443)] + [
    identity("get-feature-flag", 41),
    identity("get-feature-flags", 25),
]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


async def test_existing_local_and_cache_scenarios(contracts):
    async with serve(contracts, host_type=LocalFlagsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=IDS)
    assert strict_exit_code(contracts, report) == 0, [
        r for r in report["results"] if r["result"]["status"] not in ("passed", "not_selected")
    ]
    assert sum(r["result"]["status"] == "passed" for r in report["results"]) == 8
    assert sum(r["result"]["status"] == "not_selected" for r in report["results"]) == 720
    previous = {
        c["case_id"] for c in discover(SPECS, registry=PREVIOUS_STEPS)["cases"] if c["status"] == "harness_ready"
    }
    current = {c["case_id"] for c in discover(SPECS, registry=LOCAL_STEPS)["cases"] if c["status"] == "harness_ready"}
    assert current - previous == set(IDS)
    assert len(host.closed) == 8
    assert all(f.closed and f.engine is None and not f.references and not f.retained for f in host.fixtures.values())
    empty = next(d for d in diagnostics["cases"] if d["case_id"] == IDS[0])
    activity = [
        c["response"]["observation"]
        for c in empty["controls"]
        if c["request"]["command"]["kind"] == "evaluation_activity"
    ]
    assert len(activity) == 3
    assert all(o["cache_lookups"] == o["local_evaluations"] == 0 and o["layer"] == "native_component" for o in activity)
    calls = [c["invoke"] for c in host.inputs]
    assert {"distinct_id": "user-123", "flag_keys": []} in [c["args"] for c in calls if c["route"] == "/evaluate_flags"]
    assert {"distinct_id": "user-123", "only_evaluate_locally": True} in [
        c["args"] for c in calls if c["route"] == "/evaluate_flags"
    ]
    assert not any(c["route"] in ("/update_flags", "/flush") for c in calls)
    failure_case = next(d for d in diagnostics["cases"] if d["case_id"] == identity("evaluate-flags", 443))
    assert [r["status"] for r in failure_case["flag_requests"]] == [200, 503]


@pytest.mark.parametrize(
    "defect,line,code",
    [
        ("empty_looks_up_cache", 115, "unexpected_flag_activity"),
        ("empty_evaluates_locally", 115, "unexpected_flag_activity"),
        ("empty_evaluates_remotely", 115, "flag_request_count"),
        ("definitions_ignored", 407, "flag_value"),
        ("cache_seed_ignored", 421, "flag_value"),
        ("cache_scope_ignored", 421, "snapshot_keys"),
        ("remote_overwrites_local", 145, "flag_value"),
        ("local_only_remote", 435, "flag_request_count"),
        ("lose_local_on_failure", 443, "flag_value"),
        ("skip_fallback", 443, "flag_failure_not_exercised"),
    ],
)
async def test_native_defects_fail_then_fresh_case_passes(contracts, defect, line, code):
    first, following = identity("evaluate-flags", line), identity("get-feature-flag", 41)
    async with serve(contracts, host_type=LocalFlagsHost, defect=defect) as (host, url):
        report, _ = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[first, following]
        )
    result = next(r["result"] for r in report["results"] if r["case_id"] == first)
    assert result["status"] == "failed_assertion" and result["failure"]["code"] == code, result
    assert result["failure"]["failed_step"]["source"]["path"] == "acceptance/public/evaluate-flags.feature"
    assert result["failure"]["call_ids"]
    assert next(r["result"]["status"] for r in report["results"] if r["case_id"] == following) == "passed"
    assert strict_exit_code(contracts, report) == 1 and len(host.closed) == 2


@pytest.mark.parametrize(
    "options,status,code",
    [
        ({"missing_capability": "flags.definitions.install.v1"}, "blocked_fixture", "missing_fixture"),
        ({"missing_capability": "flags.evaluation_cache.put.v1"}, "blocked_fixture", "missing_fixture"),
        ({"missing_capability": "flags.evaluation_activity.v1"}, "blocked_fixture", "missing_fixture"),
        ({"defect": "native_component_unavailable"}, "blocked_fixture", "component_unavailable"),
        ({"defect": "wrong_native_fixture"}, "harness_error", "invalid_response"),
        ({"defect": "native_fixture_timeout"}, "harness_error", "fixture_deadline"),
        ({"missing_route": "/evaluate_flags"}, "unsupported_binding", "missing_operation"),
    ],
)
async def test_fixture_and_binding_failures_are_distinct(contracts, options, status, code):
    async with serve(contracts, host_type=LocalFlagsHost, **options) as (host, url):
        report, _ = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=IDS[:1], timeout_ms=100
        )
    result = next(r["result"] for r in report["results"] if r["case_id"] == IDS[0])
    assert result["status"] == status and result["failure"]["code"] == code, result
    assert strict_exit_code(contracts, report) == 1 and len(host.closed) == 1
    if options.get("defect") == "native_fixture_timeout":
        assert all(f.task.cancelled() and f.engine is None for f in host.fixtures.values())


def test_native_cache_uses_context_and_expiration_and_definitions_replace_atomically():
    engine = LocalFlagsEngine({}, "2025-01-01T00:00:00Z", "http://unused", None)
    engine.cache_put({"distinct_id": "one", "flags": {"f": False}, "payloads": {"f": None}})
    assert engine.activity == {"cache_lookups": 0, "local_evaluations": 0}
    assert engine.lookup({"distinct_id": "one"}) == ({"f": False}, {"f": None})
    assert engine.lookup({"distinct_id": "two"}) is None
    assert engine.lookup({"distinct_id": "one", "groups": {"company": "x"}}) is None
    engine.clock = "2025-01-01T00:05:00Z"
    assert engine.lookup({"distinct_id": "one"}) is None
    document = {"flags": [constant_definition("f", True, 1)], "cohorts": {}, "group_type_mapping": {}}
    assert not engine.ready.is_set()
    engine.install(document)
    assert engine.ready.is_set() and engine.definitions["f"]["active"] is True
    with pytest.raises(BoundaryError) as error:
        engine.install({"flags": [{"unsupported": True}], "cohorts": {}, "group_type_mapping": {}})
    assert error.value.kind == "blocked_fixture" and set(engine.definitions) == {"f"}
    engine.install({"flags": [], "cohorts": {}, "group_type_mapping": {}})
    assert engine.definitions == {} and engine.cache


@pytest.mark.parametrize("defect,code", [(None, 0), ("empty_looks_up_cache", 1)])
async def test_native_fixture_cli_reports_outside_checkout(contracts, tmp_path, defect, code):
    path = tmp_path / "native-flags.json"
    async with serve(contracts, host_type=LocalFlagsHost, defect=defect) as (host, url):
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("posthog-test-harness-v2")),
            "run",
            "--specs",
            str(SPECS),
            "--contracts",
            str(CONTRACT_PATH),
            "--all-features",
            "--case-id",
            IDS[0],
            "--adapter-url",
            url,
            "--profile",
            host.profile["id"],
            "--report",
            str(path),
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        except BaseException:
            process.kill()
            await process.wait()
            raise
    assert process.returncode == code, (stdout, stderr)
    report = json.loads(path.read_text())
    assert len(report["results"]) == 728
    assert strict_exit_code(contracts, report) == code


async def test_readiness_uses_native_signal_and_controls_do_not_become_sdk_calls(contracts):
    server = CaseServer()
    try:
        async with serve(contracts, host_type=LocalFlagsHost) as (host, url), Client(url, contracts) as client:
            async with client.fixture("native", "controlled-readiness", host.profile["id"], 1000) as fixture:
                controls = FlushControls(fixture, host.profile, 1000, [])
                await controls.command("scheduler_manual")
                await controls.command("clock_fixed", timestamp="2025-01-01T00:00:00Z")
                await controls.command("storage_empty")
                await fixture.invoke(
                    "setup", "/setup", {"project_token": "fixture-token", "config": {"host": server.url}}
                )
                for call_id, route, args in [
                    ("before", "/is_local_evaluation_ready", {}),
                    ("wait-before", "/wait_for_local_evaluation_ready", {"timeout_ms": 1}),
                ]:
                    receipt = await fixture.invoke(call_id, route, args)
                    assert receipt["completion"]["outcome"] == {"kind": "value", "value": False}
                ctx = SimpleNamespace(
                    fixture=fixture, profile=host.profile, timeout_ms=1000, diagnostics={"controls": []}
                )
                native = FlagStateControls(ctx)
                observation = await native.command(
                    "definitions_install",
                    definitions={"flags": [constant_definition("f", True, 1)], "cohorts": {}, "group_type_mapping": {}},
                )
                assert observation["layer"] == "native_component"
                for call_id, route in [
                    ("after", "/is_local_evaluation_ready"),
                    ("wait-after", "/wait_for_local_evaluation_ready"),
                ]:
                    receipt = await fixture.invoke(call_id, route, {})
                    assert receipt["completion"]["outcome"] == {"kind": "value", "value": True}
                assert len(client.calls) == 5
                assert len(ctx.diagnostics["controls"]) == 1
    finally:
        await asyncio.to_thread(server.close)
