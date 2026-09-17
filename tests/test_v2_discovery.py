"""Full-corpus route discovery and the first analytics/profile execution increment."""

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from click.testing import CliRunner

from posthog_test_harness.v2.analytics_steps import STEPS, properties, property_value
from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.discovery import discover, execution_route, feature_paths
from posthog_test_harness.v2.flag_steps import STEPS as SNAPSHOT_STEPS
from posthog_test_harness.v2.gherkin import compile_feature, load_cases
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import Registry
from tests.test_v2_gherkin import CONTRACT_PATH, PIN, SPECS
from tests.v2_analytics_host import AnalyticsHost
from tests.v2_flags_host import FlagsHost
from tests.v2_flush_host import serve

FEATURES = [f"acceptance/public/{name}.feature" for name in ("capture", "identify", "alias")]
IDS = [f"gherkin:{PIN[:7]}:{path}:L{line}" for path, line in zip(FEATURES, (32, 27, 24))]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def test_full_discovery_preserves_inventory_and_missing_steps():
    report = discover(SPECS)
    inventory = [
        json.loads(line) for line in (SPECS / "coverage/harness-v2/shared-cases.jsonl").read_text().splitlines()
    ]
    assert len(feature_paths(SPECS)) == 60
    assert {c["case_id"] for c in report["cases"]} == {c["id"] for c in inventory}
    assert len(report["cases"]) == 728
    assert all(
        c["execution_status"] == "unexecuted"
        and c["applicability"] == "unresolved"
        and c["sdk_binding_status"] == "not_negotiated"
        for c in report["cases"]
    )
    ready = [c for c in report["cases"] if c["status"] == "harness_ready"]
    assert len(ready) == 57
    assert set(IDS).issubset({c["case_id"] for c in ready})
    capture = next(c for c in ready if c["case_id"] == IDS[0])
    assert capture["required_routes"] == ["/capture", "/setup"]
    assert capture["required_fixture_capabilities"] == [
        "clock.fixed.v1",
        "queue.snapshot.v1",
        "scheduler.manual.v1",
        "storage.empty.v1",
    ]
    for case in report["cases"]:
        missing = [s for s in case["steps"] if s["status"] == "missing_harness"]
        assert case["missing_step_indexes"] == [s["index"] for s in missing]
        assert case["requirements_complete"] == (not missing)
        assert all(s["source"]["line"] > 0 and s["text"] for s in missing)


def test_discovery_cli_is_offline_and_ready_gate_is_explicit(tmp_path):
    output = tmp_path / "routes.json"
    runner = CliRunner()
    args = ["discover", "--specs", str(SPECS), "--report", str(output)]
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "728 discovered" in result.output and "All cases are unexecuted" in result.output
    assert runner.invoke(main, [*args, "--require-ready"]).exit_code == 1
    assert (
        runner.invoke(main, [*args, "--feature", "acceptance/public/flush.feature", "--require-ready"]).exit_code == 0
    )
    assert runner.invoke(main, [*args, "--feature", "missing.feature"]).exit_code != 0


def test_ambiguous_bindings_fail_discovery():
    registry = Registry()
    registry.definitions = [*STEPS.definitions, *STEPS.definitions]
    with pytest.raises(BoundaryError, match="Multiple step definitions"):
        discover(SPECS, [FEATURES[0]], registry)


def test_argument_errors_are_not_missing_harness_routes():
    case = compile_feature(
        'Feature: F\n Scenario: S\n  When capture is called with event "E"\n   | unexpected |\n',
        "f.feature",
        "revision",
    )[0]
    with pytest.raises(BoundaryError) as error:
        execution_route(case)
    assert error.value.code == "invalid_step_data"


async def test_server_analytics_profile_cases(contracts):
    async with serve(contracts, host_type=AnalyticsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, FEATURES, url, host.profile["id"], case_ids=IDS)
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["case_id"] for r in report["results"] if r["result"]["status"] == "passed"] == IDS
    assert all(r["result"]["status"] == "not_selected" for r in report["results"] if r["case_id"] not in IDS)
    assert [c["route"] for c in report["calls"]] == ["/setup", "/capture", "/setup", "/identify", "/setup", "/alias"]
    calls = [c["invoke"] for c in host.inputs]
    assert calls[1]["args"] == {"distinct_id": "user-123", "event": "Signed Up", "properties": {"source": "api"}}
    assert calls[3]["args"] == {"distinct_id": "user-123", "set": {"email": "user@test.test"}}
    assert calls[5]["args"] == {"distinct_id": "anon-123", "alias": "user-123"}
    assert len(host.closed) == 3
    assert len({c["fixture_id"] for c in diagnostics["cases"]}) == 3
    assert all(f.engine is None and not f.storage for f in host.fixtures.values())


@pytest.mark.parametrize(
    "defect,code",
    [
        ("wrong_identity", "event_identity"),
        ("missing_uuid", "event_uuid"),
        ("missing_property", "event_property"),
        ("duplicate_event", "event_count"),
        ("missing_capture", "event_count"),
        ("incorrect_result", "incorrect_result"),
    ],
)
async def test_analytics_defects_are_attributed_and_isolated(contracts, defect, code):
    async with serve(contracts, host_type=AnalyticsHost, defect=defect) as (host, url):
        report, _ = await run(contracts, SPECS, FEATURES, url, host.profile["id"], case_ids=IDS)
    assert strict_exit_code(contracts, report) == 1
    results = [r["result"] for r in report["results"] if r["case_id"] in IDS]
    assert results[0]["status"] == "failed_assertion"
    assert results[0]["failure"]["code"] == code
    assert len(results[0]["failure"]["call_ids"]) == 2
    assert results[0]["failure"]["failed_step"]["source"]["path"] == FEATURES[0]
    assert [r["status"] for r in results[1:]] == ["passed", "passed"]
    assert len(host.closed) == 3


@pytest.mark.parametrize(
    "options,status",
    [
        ({"missing_route": "/capture"}, "unsupported_binding"),
        ({"missing_capability": "queue.snapshot.v1"}, "blocked_fixture"),
    ],
)
async def test_harness_routes_do_not_mask_sdk_or_fixture_gaps(contracts, options, status):
    async with serve(contracts, host_type=AnalyticsHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURES[0]], url, host.profile["id"], case_ids=IDS[:1])
    assert strict_exit_code(contracts, report) == 1
    result = next(r for r in report["results"] if r["case_id"] == IDS[0])
    assert result["result"]["status"] == status
    assert next(r for r in report["inventory"] if r["case_id"] == IDS[0])["applicability"] == {"kind": "applicable"}


async def test_analytics_cli_outside_checkout(contracts, tmp_path):
    report_path = tmp_path / "run.json"
    async with serve(contracts, host_type=AnalyticsHost) as (host, url):
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
            str(report_path),
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
    assert process.returncode == 0, (stdout, stderr)
    report = json.loads(report_path.read_text())
    assert strict_exit_code(contracts, report) == 0
    assert len(report["results"]) == 728
    assert sum(r["result"]["status"] == "not_selected" for r in report["results"]) == 727


def test_property_table_duplicate_and_nested_lookup():
    cases, _ = load_cases(SPECS, [FEATURES[0]])
    step = deepcopy(next(s for c in cases if c.id == IDS[0] for s in c.steps if s.text.endswith("and properties:")))
    row = step.argument["dataTable"]["rows"][1]
    row["cells"][1]["value"] = "false"
    assert properties(step) == {"source": "false"}
    step.argument["dataTable"]["rows"].append(deepcopy(row))
    with pytest.raises(BoundaryError, match="Duplicate property key"):
        properties(step)
    assert property_value({"properties": {"$set": {"email": "a"}}}, "$set.email") == (True, "a")
    assert property_value({"properties": {"$set.email": "a", "$set": {"email": "b"}}}, "$set.email") == (True, "b")
    assert property_value({"properties": {"$set.email": "a"}}, "$set.email") == (False, None)
    assert property_value({"properties": {"n": None}}, "n") == (True, None)
    assert property_value({"properties": {}}, "n") == (False, None)


async def test_analytics_and_remote_snapshot_cases_retain_full_inventory(contracts):
    discovered = discover(SPECS, registry=SNAPSHOT_STEPS)
    ids = [c["case_id"] for c in discovered["cases"] if c["status"] == "harness_ready"]
    async with serve(contracts, host_type=FlagsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=ids)
    assert strict_exit_code(contracts, report) == 0, report
    assert len(report["results"]) == 728
    assert {r["case_id"] for r in report["results"] if r["result"]["status"] == "passed"} == set(ids)
    assert sum(r["result"]["status"] == "not_selected" for r in report["results"]) == 711
    assert len(host.closed) == len(diagnostics["cases"]) == 17


@pytest.mark.parametrize("defect", ["literal_profile", "masked_profile"])
async def test_identify_requires_actual_nested_profile_update(contracts, defect):
    async with serve(contracts, host_type=AnalyticsHost, defect=defect) as (host, url):
        report, _ = await run(contracts, SPECS, FEATURES[1:], url, host.profile["id"], case_ids=IDS[1:])
    results = [r["result"] for r in report["results"] if r["case_id"] in IDS[1:]]
    assert results[0]["status"] == "failed_assertion"
    assert results[0]["failure"]["code"] == "event_property"
    assert results[1]["status"] == "passed"
    assert strict_exit_code(contracts, report) == 1


async def test_unimplemented_case_is_harness_error_not_sdk_gap(contracts):
    cases, _ = load_cases(SPECS, [FEATURES[0]])
    async with serve(contracts, host_type=AnalyticsHost) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURES[0]], url, host.profile["id"], case_ids=[cases[0].id])
    result = report["results"][0]["result"]
    assert result["status"] == "harness_error"
    assert result["failure"]["code"] == "undefined_step"
    assert result["executed"] is False
    assert not host.fixtures
    assert strict_exit_code(contracts, report) == 1
