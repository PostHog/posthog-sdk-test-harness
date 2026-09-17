"""AI migration traceability, two-level selection and deliberate wire/result defects."""

import hashlib
import json
import shutil
from copy import deepcopy

import pytest
from click.testing import CliRunner

from posthog_test_harness.v2.ai_steps import json_arguments, utc_instant
from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import SUITE, migration_paths
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS, cli_run
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve

FEATURE = SUITE + "/capture-ai.feature"
CASES, _ = load_cases(SPECS, [FEATURE])
IDS = [c.id for c in CASES]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def test_migration_has_separate_identities_and_exact_legacy_traceability():
    legacy = [json.loads(line) for line in (SPECS / "coverage/harness-v2/legacy-cases.jsonl").read_text().splitlines()]
    legacy = [c for c in legacy if c["source"]["path"] == "contracts/capture_ai_tests.yaml"]
    assert len(CASES) == len(legacy) == 5
    assert migration_paths(SPECS) == [
        FEATURE,
        SUITE + "/capture-analytics-v1.feature",
        SUITE + "/capture-analytics-v1-batching.feature",
        SUITE + "/capture-analytics-v1-retry.feature",
        SUITE + "/capture-analytics-v1-outcomes.feature",
        SUITE + "/capture-legacy.feature",
        SUITE + "/capture-amendment-v1.feature",
        SUITE + "/remote-flags-v1.feature",
        SUITE + "/local-evaluation-v1.feature",
    ]
    for case, origin in zip(CASES, legacy):
        assert case.migration["legacy_id"] == origin["id"]
        assert case.migration["legacy_source"] == origin["source"]
        assert case.migration["legacy_filters"] == origin["capability_filters"]
        assert case.id.startswith("migration:yaml-parity-v1:")
        assert "@both" in case.tags and "@api_capture_ai_v0" in case.tags
        invocation = next(s for s in case.steps if s.argument.get("docString"))
        action = next(s for s in origin["steps"] if s["input"]["action"] in ("capture", "capture_ai"))
        assert json_arguments(invocation) == action["input"]["params"]
    discovery = discover(SPECS, [FEATURE])
    assert all(c["status"] == "harness_ready" and c["execution_status"] == "unexecuted" for c in discovery["cases"])
    assert all(c["required_fixture_capabilities"] == ["storage.empty.v1"] for c in discovery["cases"])
    canonical = discover(SPECS)
    assert len(canonical["cases"]) == 728
    assert sum(c["status"] == "harness_ready" for c in canonical["cases"]) == 57
    assert not set(IDS) & {c["case_id"] for c in canonical["cases"]}


@pytest.mark.parametrize("corruption", ["feature", "ledger", "mapping"])
def test_migration_rejects_changed_sources_and_incomplete_mapping(tmp_path, corruption):
    shutil.copytree(SPECS / "coverage/harness-v2", tmp_path / "coverage/harness-v2")
    shutil.copytree(SPECS / SUITE, tmp_path / SUITE)
    if corruption == "feature":
        with (tmp_path / FEATURE).open("a") as file:
            file.write("\n# changed\n")
    else:
        ledger = tmp_path / SUITE / "cases.json"
        rows = json.loads(ledger.read_text())
        rows.pop(0)
        ledger.write_text(json.dumps(rows))
        if corruption == "mapping":
            path = tmp_path / SUITE / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["ledger"]["sha256"] = hashlib.sha256(ledger.read_bytes()).hexdigest()
            path.write_text(json.dumps(manifest))
    with pytest.raises(BoundaryError):
        load_cases(tmp_path, [FEATURE])


@pytest.mark.parametrize(
    "content,media", [('{"event":false,"event":true}', "application/json"), ("{}", "json"), ("[]", "application/json")]
)
def test_migration_argument_docstrings_are_strict(content, media):
    step = deepcopy(next(s for s in CASES[0].steps if "docString" in s.argument))
    step.argument["docString"].update(content=content, mediaType=media)
    with pytest.raises(BoundaryError):
        json_arguments(step)


@pytest.mark.parametrize("runtime", ["server", "browser", "mobile", "edge"])
@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
async def test_all_five_execute_independently_of_sdk_role(contracts, runtime, protocol):
    async with serve(contracts, host_type=AIHost, runtime=runtime, protocol=protocol) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 5
    assert len(host.closed) == 5 and len(report["calls"]) == 15
    assert all(f.engine is None and f.closed for f in host.fixtures.values())
    calls = [row["invoke"] for row in host.inputs]
    assert [call["route"] for call in calls] == [
        route
        for operation in ("/capture_ai", "/capture", "/capture_ai", "/capture_ai", "/capture_ai")
        for route in ("/setup", operation, "/flush")
    ]
    assert all(
        call["args"]["config"]["flush_at"] == 1 and call["args"]["project_token"] == "phc_test_key"
        for call in calls
        if call["route"] == "/setup"
    )
    for index, case in enumerate(CASES):
        assert calls[index * 3 + 1]["args"] == json_arguments(next(s for s in case.steps if "docString" in s.argument))
    assert diagnostics["cases"][0]["ingestion"][0]["path"] == "/i/v0/ai/batch/"
    assert diagnostics["cases"][1]["ingestion"][0]["path"] == (
        "/batch" if protocol == "legacy" else "/i/v1/analytics/events"
    )
    assert all(len(d["network"]) == 1 for d in diagnostics["cases"])


@pytest.mark.parametrize(
    "options,statuses,code",
    [
        ({"sdk_capabilities": None}, ["not_selected"] * 5, None),
        ({"sdk_capabilities": []}, ["not_selected"] * 5, None),
        ({"sdk_capabilities": [], "missing_route": "/capture_ai"}, ["not_selected"] * 5, None),
        (
            {"missing_route": "/capture_ai"},
            ["unsupported_binding", "passed", "unsupported_binding", "unsupported_binding", "unsupported_binding"],
            "missing_operation",
        ),
        ({"missing_route": "/capture"}, ["passed", "not_selected", "passed", "passed", "passed"], None),
        ({"missing_route": "/setup"}, ["unsupported_binding"] * 5, "missing_operation"),
        ({"missing_route": "/flush"}, ["unsupported_binding"] * 5, "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, ["blocked_fixture"] * 5, "fixture_unavailable"),
    ],
)
async def test_automatic_selection_distinguishes_claims_operations_and_fixtures(contracts, options, statuses, code):
    async with serve(contracts, host_type=AIHost, **options) as (host, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert [r["result"]["status"] for r in report["results"]] == statuses
    assert strict_exit_code(contracts, report) == (0 if "passed" in statuses and not code else 1)
    for row in report["results"]:
        result = row["result"]
        if "failure" in result:
            assert result["failure"]["code"] == code and not result["executed"]
        if result["status"] == "not_selected":
            assert "unavailable" in result["reason"] or "not declared" in result["reason"]
    assert len(diagnostics["selection"]) == 5


@pytest.mark.parametrize(
    "options,index,code",
    [
        ({"sdk_capabilities": None}, 0, "sdk_capability_unavailable"),
        ({"missing_route": "/capture"}, 1, "missing_operation"),
        ({"missing_route": "/capture_ai", "sdk_capabilities": []}, 0, "missing_operation"),
    ],
)
async def test_explicit_selection_never_hides_prerequisite_gaps(contracts, options, index, code):
    async with serve(contracts, host_type=AIHost, **options) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"], case_ids=[IDS[index]])
    result = report["results"][index]["result"]
    assert result["status"] == "unsupported_binding" and result["failure"]["code"] == code
    assert strict_exit_code(contracts, report) == 1
    assert not host.fixtures


@pytest.mark.parametrize(
    "defect,index,code",
    [
        ("wrong_route", 0, "request_path"),
        ("wrong_event", 0, "event_field"),
        ("duplicate_request", 0, "request_count"),
        ("startup_capture", 0, "request_count"),
        ("startup_flags", 0, "request_count"),
        ("no_delivery", 0, "request_count"),
        ("reroute", 1, "request_path"),
        ("missing_uuid", 2, "event_uuid"),
        ("invalid_uuid", 2, "event_uuid"),
        ("null_result", 2, "ai_not_admitted"),
        ("returned_uuid", 2, "uuid_mismatch"),
        ("replace_supplied_uuid", 3, "returned_uuid"),
        ("wire_uuid", 3, "event_field"),
        ("offset_timestamp", 4, "event_timestamp"),
        ("wrong_instant", 4, "event_timestamp"),
        ("nanosecond_drift", 4, "event_timestamp"),
        ("second_event_timestamp", 4, "event_timestamp"),
        ("rewrite_property", 4, "event_property"),
    ],
)
async def test_migrated_assertions_reject_deliberate_defects(contracts, defect, index, code):
    async with serve(contracts, host_type=AIHost, defect=defect, defect_case=index) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 1
    for i, row in enumerate(report["results"]):
        result = row["result"]
        if i == index:
            assert result["status"] == "failed_assertion", result
            assert result["failure"]["code"] == code
            assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
            assert len(result["failure"]["call_ids"]) >= 2
        else:
            assert result["status"] == "passed", result
    assert len(host.closed) == 5


def test_utc_timestamp_comparison_preserves_nanoseconds_and_utc_syntax():
    assert utc_instant("2025-01-02T03:04:05.000000000Z") == utc_instant("2025-01-02T03:04:05+00:00")
    assert utc_instant("2025-01-02T03:04:05.000000001Z") != utc_instant("2025-01-02T03:04:05Z")
    for value in [None, "2025-01-02T08:34:05+05:30", "2025-02-30T03:04:05Z"]:
        assert utc_instant(value) is None


@pytest.mark.parametrize("defect,exit_code", [(None, 0), ("wrong_route", 1)])
async def test_migration_cli_outside_checkout(contracts, tmp_path, defect, exit_code):
    async with serve(contracts, host_type=AIHost, defect=defect) as (host, url):
        code, report, _, output = await cli_run(tmp_path, url, "--migration-suite", "--profile", host.profile["id"])
    assert code == strict_exit_code(contracts, report) == exit_code, output
    assert len(report["results"]) == 157
    assert all(row["case_id"] in IDS for row in report["results"][:5])
    assert all(row["result"]["status"] == "not_selected" for row in report["results"][5:])


def test_migration_discovery_cli(tmp_path):
    output = tmp_path / "discovery.json"
    result = CliRunner().invoke(
        main, ["discover", "--specs", str(SPECS), "--migration-suite", "--require-ready", "--report", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert "157 discovered, 157 harness-ready" in result.output
    assert len(json.loads(output.read_text())["cases"]) == 157
    missing = CliRunner().invoke(
        main, ["discover", "--specs", str(tmp_path), "--migration-suite", "--report", str(output)]
    )
    assert missing.exit_code == 1 and "Missing or malformed migration manifest" in missing.output
