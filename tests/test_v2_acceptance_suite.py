"""One acceptance run retains canonical opt-in and migrated capability selection."""

import json

import pytest
from click.testing import CliRunner

from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.discovery import acceptance_paths
from posthog_test_harness.v2.migration import migration_paths
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve


@pytest.fixture
def corpus(tmp_path):
    setup = (
        "  Given an isolated SDK instance\n"
        '  When the SDK is initialized with token "test-token" and flush threshold 20\n'
    )
    for path, scenarios in {
        "migration/yaml-parity-v1/mixed.feature": [
            ("@requires:capture_ai_v0", "Available capability"),
            ("@requires:unavailable", "Unavailable capability"),
            ("@sdk:client", "Client context"),
            ("", "Shared migrated case"),
        ],
        "acceptance/public/mixed.feature": [
            ("@sdk:server", "Server opt-in"),
            ("@sdk:client", "Client opt-in"),
            ("@sdk:server @sdk:client", "Shared opt-in"),
            ("@sdk:server @requires:unavailable", "Selected binding gap"),
            ("@server @both", "Description tags only"),
        ],
    }.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True)
        target.write_text(
            "Feature: Selection\n" + "".join(f" {tags}\n Scenario: {name}\n{setup}" for tags, name in scenarios)
        )
    return tmp_path


@pytest.mark.parametrize("runtime", ["server", "browser"])
@pytest.mark.parametrize("missing_route", [None, "/setup"])
async def test_unified_run_matches_the_union_of_previous_runs(corpus, runtime, missing_route):
    contracts = Contracts()
    paths = acceptance_paths(corpus)
    canonical = [path for path in paths if path.startswith("acceptance/")]

    async def execute(features, tagged):
        async with serve(contracts, host_type=AIHost, runtime=runtime, missing_route=missing_route) as (host, url):
            return await run(contracts, corpus, features, url, host.profile["id"], tagged_acceptance=tagged)

    migrated, _ = await execute(migration_paths(corpus), False)
    opted_in, _ = await execute(canonical, True)
    unified, diagnostics = await execute(paths, True)

    def outcomes(report):
        return {
            row["case_id"]: (
                row["result"]["status"],
                row["result"]["executed"],
                row["result"].get("failure", {}).get("code"),
            )
            for row in report["results"]
        }

    assert outcomes(unified) == {**outcomes(migrated), **outcomes(opted_in)}
    assert len(unified["results"]) == len(migrated["results"]) + len(opted_in["results"]) == 9
    assert unified["inventory"] == sorted(
        migrated["inventory"] + opted_in["inventory"], key=lambda row: row["source"]["path"]
    )
    assert strict_exit_code(contracts, unified) == max(
        strict_exit_code(contracts, migrated), strict_exit_code(contracts, opted_in)
    )
    assert any(case.get("migration") is not None for case in diagnostics["discovered"])
    assert not unified["errors"]


def test_unified_discovery_and_migration_compatibility_filter(corpus, tmp_path):
    runner = CliRunner()
    report = tmp_path / "discovery.json"
    args = ["discover", "--specs", str(corpus), "--require-ready", "--report", str(report)]
    unified = runner.invoke(main, [*args, "--acceptance-suite"])
    assert unified.exit_code == 0, unified.output
    cases = json.loads(report.read_text())["cases"]
    assert len(cases) == 8
    assert len({case["case_id"] for case in cases}) == 8
    assert {case["name"] for case in cases} >= {"Shared migrated case", "Server opt-in", "Client opt-in"}
    assert "Description tags only" not in {case["name"] for case in cases}

    migrated = runner.invoke(main, [*args, "--migration-suite"])
    assert migrated.exit_code == 0, migrated.output
    assert len(json.loads(report.read_text())["cases"]) == 4
