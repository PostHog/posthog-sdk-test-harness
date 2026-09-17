"""Phase-3 proof: original Gherkin, real HTTP, CLI receipts and deliberate defects."""

import asyncio
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import aiohttp
import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts, decode_json
from posthog_test_harness.v2.data import doc_string
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.gherkin import compile_feature, load_cases
from posthog_test_harness.v2.report import strict_exit_code, validate_report
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import FLUSH_STEPS, Registry, contains_events, table
from tests.v2_flush_host import PROFILE, serve

CONTRACT_PATH = Path(os.environ.get("SDK_V2_CONTRACTS", Path(__file__).resolve().parents[2] / "specs/contracts/v2"))
SPECS = CONTRACT_PATH.parent.parent
FEATURE = "acceptance/public/flush.feature"
PIN = "9cb330e3bac8868f39cc7dd665e42817285c9493"
IDS = [f"gherkin:{PIN[:7]}:{FEATURE}:L{line}" for line in (12, 26, 34)]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


async def cli_run(tmp_path, url, *extra, specs=SPECS):
    report_path = tmp_path / "report.json"
    process = await asyncio.create_subprocess_exec(
        str(Path(sys.executable).with_name("posthog-test-harness-v2")),
        "run",
        "--specs",
        str(specs),
        "--contracts",
        str(CONTRACT_PATH),
        "--adapter-url",
        url,
        "--profile",
        PROFILE["id"],
        "--timeout-ms",
        "1000",
        "--report",
        str(report_path),
        *extra,
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
    assert report_path.exists(), (stdout.decode(), stderr.decode())
    report = decode_json(report_path.read_bytes())
    diagnostics = decode_json(report_path.with_name("report.json.diagnostics.json").read_bytes())
    return process.returncode, report, diagnostics, stdout.decode()


def synthetic_specs(tmp_path, text):
    root = tmp_path / "specs"
    feature = root / FEATURE
    feature.parent.mkdir(parents=True)
    feature.write_text(text)
    manifest = root / "coverage/harness-v2/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "revision": "controlled-test-input",
                        "path": FEATURE,
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    }
                ]
            }
        )
    )
    return root


async def test_existing_flush_feature_cli_end_to_end(contracts, tmp_path):
    async with serve(contracts) as (host, url):
        code, report, diagnostics, output = await cli_run(tmp_path, url)
    assert code == strict_exit_code(contracts, report) == 0, (report, output)
    validate_report(contracts, report)
    assert [r["case_id"] for r in report["results"]] == IDS
    assert [r["result"]["status"] for r in report["results"]] == ["passed"] * 3
    assert [c["route"] for c in report["calls"]] == [
        "/setup",
        "/capture",
        "/capture",
        "/flush",
        "/setup",
        "/flush",
        "/setup",
        "/capture",
        "/flush",
    ]
    assert len(host.closed) == len(host.fixtures) == 3
    assert all(f.closed and not f.storage and not f.references and f.engine is None for f in host.fixtures.values())
    assert len({r["invoke"]["args"]["config"]["host"] for r in host.inputs if r["invoke"]["route"] == "/setup"}) == 3
    assert len({r["invoke"]["receiver"]["id"] for r in host.inputs}) == 3
    assert "3 passed" in output and "Strict gate: passed" in output
    assert diagnostics["inputs"][0]["revision"] == PIN
    assert diagnostics["run_id"] == report["run_id"]
    first, empty, retry = diagnostics["cases"]
    assert empty["network"] == [{**empty["network"][0], "method": "POST", "path": "/flags", "status": 200}]
    assert empty["flush_window_start"] == empty["flush_window_end"] == 1
    assert first["ingestion"][-1] == {"path": "/batch", "status": 200, "event_names": ["First", "Second"]}
    assert retry["ingestion"][-1] == {"path": "/batch", "status": 503, "event_names": ["Save"]}
    snapshots = [
        r["response"]["observation"] for r in retry["controls"] if r["request"]["command"]["kind"] == "queue_snapshot"
    ]
    assert snapshots[0]["records"] == []
    assert snapshots[1] == snapshots[2]
    assert snapshots[1]["records"][0]["event"]["timestamp"] == "2025-01-01T00:00:00Z"
    assert "session_id" not in json.dumps(report) and host.session not in json.dumps(diagnostics)


@pytest.mark.parametrize(
    "defect,status,error,step,calls",
    [
        ("missing_event", "failed_assertion", "missing_event", 7, 4),
        ("incorrect_result", "failed_assertion", "incorrect_result", 6, 4),
        ("http", "harness_error", "http_error", 6, 4),
        ("blocked", "blocked_fixture", "component_unavailable", 5, 1),
        ("wrong_fixture", "harness_error", "invalid_response", 5, 1),
        ("timeout", "harness_error", "host_deadline", 6, 4),
        ("keep_delivered", "failed_assertion", "queue_not_drained", 8, 4),
        ("missing_capture", "failed_assertion", "queue_precondition", 5, 3),
        ("thrown", "failed_assertion", "incorrect_result", 6, 4),
        ("teardown", "harness_error", "http_error", 8, 4),
    ],
)
async def test_cli_defects_fail_then_fresh_case_passes(contracts, tmp_path, defect, status, error, step, calls):
    async with serve(contracts, defect=defect) as (host, url):
        code, report, diagnostics, output = await cli_run(tmp_path, url, "--timeout-ms", "100")
    assert code == strict_exit_code(contracts, report) == 1, output
    validate_report(contracts, report)
    first, second, third = [r["result"] for r in report["results"]]
    assert first["status"] == status, first
    assert first["failure"]["code"] == error
    assert first["failure"]["failed_step"]["index"] == step
    assert first["failure"]["failed_step"]["source"]["revision"] == PIN
    assert len(first["failure"]["call_ids"]) == calls
    assert second["status"] == third["status"] == "passed"
    assert len(host.closed) == 3
    assert all(f.engine is None and f.closed and not f.references for f in host.fixtures.values())
    if defect == "timeout":
        assert host.timeouts == 1
        assert all(f.task is None or f.task.done() for f in host.fixtures.values())
    assert "Strict gate: not passed" in output


@pytest.mark.parametrize(
    "defect,error",
    [
        ("lose_retry", "retry_record_missing"),
        ("replace_retry", "retry_record_missing"),
        ("no_delivery", "failure_not_exercised"),
    ],
)
async def test_retry_assertion_uses_original_queue_record_and_real_failed_send(contracts, tmp_path, defect, error):
    async with serve(contracts, defect=defect, defect_case=2) as (_, url):
        code, report, _, _ = await cli_run(tmp_path, url)
    assert code == 1
    validate_report(contracts, report)
    result = report["results"][2]["result"]
    assert result["status"] == "failed_assertion" and result["failure"]["code"] == error
    assert result["failure"]["failed_step"]["source"]["line"] == 42


async def test_no_network_assertion_includes_unknown_paths(contracts, tmp_path):
    async with serve(contracts, defect="empty_network", defect_case=1) as (_, url):
        code, report, diagnostics, _ = await cli_run(tmp_path, url)
    assert code == 1
    assert report["results"][1]["result"]["failure"]["code"] == "assertion_failed"
    assert diagnostics["cases"][1]["network"][-1]["path"] == "/unknown"
    assert report["results"][2]["result"]["status"] == "passed"


@pytest.mark.parametrize(
    "capability", ["queue.snapshot.v1", "clock.fixed.v1", "storage.empty.v1", "scheduler.manual.v1"]
)
async def test_missing_fixture_capability_stays_blocked(contracts, tmp_path, capability):
    async with serve(contracts, missing_capability=capability) as (host, url):
        code, report, _, _ = await cli_run(tmp_path, url)
    assert code == 1
    assert all(r["result"]["status"] == "blocked_fixture" for r in report["results"])
    assert all(capability in r["result"]["failure"]["message"] for r in report["results"])
    assert len(host.closed) == 3
    validate_report(contracts, report)


async def test_missing_public_binding_is_not_applicability(contracts, tmp_path):
    async with serve(contracts, missing_route="/flush") as (host, url):
        code, report, _, _ = await cli_run(tmp_path, url)
    assert code == 1
    assert all(r["result"]["status"] == "unsupported_binding" for r in report["results"])
    assert all(r["applicability"] == {"kind": "applicable"} for r in report["inventory"])
    assert all(r["invoke"]["route"] != "/flush" for r in host.inputs)
    validate_report(contracts, report)


async def test_rejected_negotiation_does_not_allocate(contracts, tmp_path):
    async with serve(contracts, defect="rejected") as (host, url):
        code, report, diagnostics, _ = await cli_run(tmp_path, url)
    assert code == 1 and not host.fixtures
    assert report["errors"][0]["code"] == "incompatible_adapter"
    assert len(diagnostics["discovered"]) == 3
    validate_report(contracts, report)


async def test_unknown_step_fails_discovery_before_case_execution(contracts, tmp_path):
    text = (
        (SPECS / FEATURE)
        .read_text()
        .replace("Then the mock server should receive a batch containing events:", "Then this step has no definition:")
    )
    specs = synthetic_specs(tmp_path, text)
    async with serve(contracts) as (host, url):
        code, report, _, _ = await cli_run(tmp_path, url, specs=specs)
    assert code == 1
    first = report["results"][0]["result"]
    assert first["status"] == "harness_error" and first["executed"] is False
    assert first["failure"]["code"] == "undefined_step"
    assert first["failure"]["failed_step"]["source"]["line"] == 19
    assert first["failure"]["call_ids"] == []
    assert len(host.fixtures) == 2
    assert [r["result"]["status"] for r in report["results"]][1:] == ["passed", "passed"]
    validate_report(contracts, report)


async def test_ambiguous_step_fails_before_allocation(contracts):
    registry = Registry()
    registry.definitions = FLUSH_STEPS.definitions * 2
    async with serve(contracts) as (host, url):
        report, _ = await run(contracts, SPECS, [FEATURE], url, PROFILE["id"], registry=registry)
    assert strict_exit_code(contracts, report) == 1 and not host.fixtures
    assert all(r["result"]["failure"]["code"] == "ambiguous_step" for r in report["results"])


async def test_selector_retains_unselected_cases_and_unknown_selector_fails(contracts, tmp_path):
    async with serve(contracts) as (host, url):
        code, report, _, _ = await cli_run(tmp_path, url, "--case-id", IDS[1])
        assert code == 0
        assert [r["result"]["status"] for r in report["results"]] == ["not_selected", "passed", "not_selected"]
        assert len(host.fixtures) == 1
        code, report, _, _ = await cli_run(tmp_path, url, "--case-id", "does-not-exist")
    assert code == 1
    assert len(host.fixtures) == 1
    assert all(r["result"]["status"] == "not_selected" for r in report["results"])
    assert report["errors"][0]["code"] == "invalid_selector"
    validate_report(contracts, report)


@pytest.mark.parametrize(
    "text,error",
    [
        ("Feature: Empty\n", "zero_cases"),
        ("not a feature", "parse_error"),
        ("Feature: Empty\n  Scenario: No steps\n", "empty_case"),
        (
            "Feature: Empty\n  Scenario Outline: No rows\n    Given something\n    Examples:\n      | a |\n",
            "empty_outline",
        ),
    ],
)
async def test_invalid_discovery_cli_cannot_pass(contracts, tmp_path, text, error):
    specs = synthetic_specs(tmp_path, text)
    async with serve(contracts) as (host, url):
        code, report, _, _ = await cli_run(tmp_path, url, specs=specs)
    assert code == 1 and not host.fixtures
    assert report["errors"][0]["code"] == error
    validate_report(contracts, report)


async def test_changed_source_cannot_claim_frozen_pin(contracts, tmp_path):
    specs = synthetic_specs(tmp_path, (SPECS / FEATURE).read_text())
    with (specs / FEATURE).open("a") as file:
        file.write("\n# changed after selection\n")
    async with serve(contracts) as (_, url):
        code, report, _, _ = await cli_run(tmp_path, url, specs=specs)
    assert code == 1 and report["errors"][0]["code"] == "source_mismatch"


def test_official_compiler_preserves_entire_frozen_case_inventory():
    paths = sorted(str(p.relative_to(SPECS)) for p in (SPECS / "acceptance").rglob("*.feature"))
    cases, _ = load_cases(SPECS, paths)
    ledger = [json.loads(line) for line in (SPECS / "coverage/harness-v2/shared-cases.jsonl").read_text().splitlines()]
    assert len(cases) == 728
    assert [c.id for c in cases] == [c["id"] for c in ledger]
    for case, frozen in zip(cases, ledger):
        assert case.source == frozen["source"] and case.tags == frozen["tags"]
        assert [(s.text, s.source, s.argument) for s in case.steps] == [
            (s["text"], s["source"], s.get("argument", {})) for s in frozen["steps"]
        ]


def test_official_outlines_backgrounds_rules_tags_and_typed_arguments():
    text = '''@feature
Feature: Types
  Background:
    Given background
  @rule
  Rule: One
    Background:
      Given rule background
    @outline
    Scenario Outline: Typed <label>
      When a table:
        | string | json   |
        | false  | <json> |
      Then a document:
        """application/json
        {"value": <json>, "label": "<label>"}
        """
      @examples
      Examples:
        | label | json  |
        | zero  | 0     |
        | false | false |
        | null  | null  |
'''
    cases = compile_feature(text, "typed.feature", "test-revision")
    assert len(cases) == 3
    for case, value in zip(cases, [0, False, None]):
        assert case.tags == ["@feature", "@rule", "@outline", "@examples"]
        assert [s.text for s in case.steps[:2]] == ["background", "rule background"]
        row = table(case.steps[2], {"string": "string", "json": "json"})[0]
        assert row["string"] == "false" and type(row["json"]) is type(value) and row["json"] == value
        arg = case.steps[3].argument["docString"]
        assert type(doc_string(arg["content"], arg["mediaType"])["value"]) is type(value)
        assert case.steps[2].source["line"] == 11 and ":example-L" in case.id
    assert len({c.id for c in cases}) == 3


def test_table_does_not_silently_coerce_or_drop_columns():
    text = "Feature: Data\n  Scenario: One\n    Given table:\n      | json | json |\n      | 0 | false |\n"
    step = compile_feature(text, "data.feature", "test")[0].steps[0]
    with pytest.raises(BoundaryError, match="duplicate"):
        table(step, {"json": "json"})
    assert not contains_events([{"event": "One"}], [{"event": "One"}, {"event": "One"}])
    assert not contains_events([{"event": False}], [{"event": 0}])


async def test_retired_mock_url_cannot_leak_into_next_case():
    first, second = CaseServer(), CaseServer()
    try:
        first.fail_next_ingestion(503)
        async with aiohttp.ClientSession() as session:
            async with session.post(first.url + "/flags", json={}) as response:
                assert response.status == 200
            async with session.post(first.url + "/batch", json={"batch": [{"event": "Old"}]}) as response:
                assert response.status == 503
            first.retire()
            async with session.post(first.url + "/batch", json={"batch": [{"event": "Late"}]}) as response:
                assert response.status == 410
            async with session.post(second.url + "/batch", json={"batch": [{"event": "New"}]}) as response:
                assert response.status == 200
        assert len(first.requests()) == 2
        assert len(second.requests()) == 1
        assert second.state.get_requests()[0].parsed_events == [{"event": "New"}]
    finally:
        await asyncio.to_thread(first.close)
        await asyncio.to_thread(second.close)


async def test_malformed_generated_report_cannot_exit_success(contracts, tmp_path, monkeypatch):
    from click.testing import CliRunner

    from posthog_test_harness.v2 import cli

    async with serve(contracts) as (_, url):
        report, diagnostics = await run(contracts, SPECS, [FEATURE], url, PROFILE["id"])
    malformed = deepcopy(report)
    malformed["results"].pop()

    async def broken_runner(*args, **kwargs):
        return malformed, diagnostics

    monkeypatch.setattr(cli, "execute", broken_runner)

    def invoke_cli():
        return CliRunner().invoke(
            cli.main,
            [
                "run",
                "--specs",
                str(SPECS),
                "--contracts",
                str(CONTRACT_PATH),
                "--adapter-url",
                "http://127.0.0.1:1",
                "--profile",
                PROFILE["id"],
                "--report",
                str(tmp_path / "report.json"),
            ],
        )

    result = await asyncio.to_thread(invoke_cli)
    assert result.exit_code == 2 and "invalid report" in result.output
