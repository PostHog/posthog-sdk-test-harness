"""Cached getters use genuine public preparation, typed results and exposure checks."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from posthog_test_harness.v2.cached_flag_steps import STEPS as CACHED_STEPS
from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.discovery import discover, feature_paths
from posthog_test_harness.v2.flag_steps import STEPS as SNAPSHOT_STEPS
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.test_v2_gherkin import CONTRACT_PATH, PIN, SPECS, synthetic_specs
from tests.test_v2_gherkin import FEATURE as SYNTHETIC_FEATURE
from tests.v2_cached_flags_host import CachedFlagsHost
from tests.v2_flush_host import serve


def identity(path, suffix):
    return f"gherkin:{PIN[:7]}:acceptance/{path}.feature:{suffix}"


IDS = [
    identity(path, suffix)
    for path, suffixes in {
        "private/feature-flag-cache": ["L23", "L32"],
        "private/feature-flag-called-tracker": ["L12", "L25", "L35", "L72"],
        "public/get-feature-flag-payload": ["L12", "L23", "L31"],
        "public/get-feature-flag-result": ["L12", "L26", "L39"],
        "public/get-feature-flag": ["L12", "L22", "L31"],
        "public/get-feature-flags-and-payloads": ["L12", "L29"],
        "public/get-feature-flags": ["L12"],
        "public/is-feature-enabled": [
            "L12:example-L22",
            "L12:example-L23",
            "L12:example-L24",
            "L27:example-L35",
            "L27:example-L36",
            "L39:example-L49",
            "L39:example-L50",
            "L53",
        ],
    }.items()
    for suffix in suffixes
]


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


async def test_existing_cached_cases_in_full_inventory(contracts):
    async with serve(contracts, host_type=CachedFlagsHost) as (host, url):
        report, diagnostics = await run(contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=IDS)
    assert strict_exit_code(contracts, report) == 0, [
        r for r in report["results"] if r["result"]["status"] not in ("passed", "not_selected")
    ]
    assert len(report["results"]) == 728
    assert sum(r["result"]["status"] == "passed" for r in report["results"]) == 26
    assert sum(r["result"]["status"] == "not_selected" for r in report["results"]) == 702
    prior = {c["case_id"] for c in discover(SPECS, registry=SNAPSHOT_STEPS)["cases"] if c["status"] == "harness_ready"}
    current = {c["case_id"] for c in discover(SPECS, registry=CACHED_STEPS)["cases"] if c["status"] == "harness_ready"}
    assert current - prior == set(IDS)
    assert len(host.closed) == 26
    assert all(not f.storage and not f.references and f.engine is None for f in host.fixtures.values())
    assert host.profile["identity"] == "stateful_installation"
    assert all(len(d["flag_requests"]) == 1 for d in diagnostics["cases"])  # startup only
    calls = [entry["invoke"] for entry in host.inputs]
    assert not any(call["route"] == "/flush" for call in calls)
    assert all(set(call["args"]["config"]) == {"host"} for call in calls if call["route"] == "/setup")
    assert all("default_value" not in call["args"] for call in calls if call["route"] == "/get_feature_flag")
    enabled_args = [call["args"] for call in calls if call["route"] == "/is_feature_enabled"]
    assert {"key": "feature"} in enabled_args
    assert {"key": "feature", "send_event": False} in enabled_args
    assert {"key": "feature", "default_value": True} in enabled_args
    assert {"key": "feature", "default_value": False} in enabled_args
    assert {"flags": {"beta-ui": False}, "merge": True} in [c["args"] for c in calls if c["route"] == "/update_flags"]
    for path, route, expected in [
        ("public/get-feature-flag", "/get_feature_flag", {"key": "checkout"}),
        ("public/get-feature-flag-result", "/get_feature_flag_result", {"key": "checkout"}),
    ]:
        case_id = identity(path, "L22" if path.endswith("/get-feature-flag") else "L12")
        fixture_id = next(d["fixture_id"] for d in diagnostics["cases"] if d["case_id"] == case_id)
        inputs = [c["invoke"] for c in host.inputs if c["fixture_id"] == fixture_id]
        assert [c["route"] for c in inputs] == ["/setup", "/update_flags", route]
        assert inputs[-1]["args"] == expected


@pytest.mark.parametrize(
    "defect,path,suffix,code",
    [
        ("cache_not_updated", "public/get-feature-flag", "L12", "flag_value"),
        ("wrong_cached_value", "public/get-feature-flag", "L22", "flag_value"),
        ("ignored_tracking_option", "public/get-feature-flag", "L31", "unexpected_event"),
        ("false_is_missing", "public/is-feature-enabled", "L39:example-L49", "flag_value"),
        ("payload_exposure", "public/get-feature-flag-payload", "L31", "unexpected_event"),
        ("wrong_result_key", "public/get-feature-flag-result", "L12", "flag_result"),
        ("wrong_result_variant", "public/get-feature-flag-result", "L26", "flag_result"),
        ("missing_result_variant", "public/get-feature-flag-result", "L26", "incorrect_result"),
        ("extra_bulk_flag", "public/get-feature-flags", "L12", "flag_values"),
        ("wrong_paired_payload", "public/get-feature-flags-and-payloads", "L12", "flag_payloads"),
        ("cache_not_reset", "private/feature-flag-cache", "L32", "flag_values"),
        ("cached_read_network", "private/feature-flag-cache", "L23", "flag_request_count"),
        ("duplicate_exposure", "private/feature-flag-called-tracker", "L25", "flag_exposure_count"),
        ("string_exposure", "private/feature-flag-called-tracker", "L12", "event_property"),
        ("null_as_void", "public/get-feature-flag-payload", "L23", "incorrect_result"),
    ],
)
async def test_cached_defects_and_following_isolated_case(contracts, defect, path, suffix, code):
    first = identity(path, suffix)
    following = identity("public/is-feature-enabled", "L53")
    async with serve(contracts, host_type=CachedFlagsHost, defect=defect) as (host, url):
        report, _ = await run(
            contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[first, following]
        )
    result = next(r["result"] for r in report["results"] if r["case_id"] == first)
    assert result["status"] == "failed_assertion" and result["failure"]["code"] == code, result
    assert result["failure"]["failed_step"]["source"]["path"] == f"acceptance/{path}.feature"
    assert len(result["failure"]["call_ids"]) >= 3
    assert next(r["result"]["status"] for r in report["results"] if r["case_id"] == following) == "passed"
    assert strict_exit_code(contracts, report) == 1
    assert len(host.closed) == 2


@pytest.mark.parametrize("route", ["/update_flags", "/get_feature_flag"])
async def test_missing_public_cache_binding_stays_applicable(contracts, route):
    selected = identity("public/get-feature-flag", "L12")
    async with serve(contracts, host_type=CachedFlagsHost, missing_route=route) as (host, url):
        report, _ = await run(contracts, SPECS, feature_paths(SPECS), url, host.profile["id"], case_ids=[selected])
    result = next(r["result"] for r in report["results"] if r["case_id"] == selected)
    assert result["status"] == "unsupported_binding"
    assert next(r["applicability"] for r in report["inventory"] if r["case_id"] == selected) == {"kind": "applicable"}
    assert strict_exit_code(contracts, report) == 1
    assert len(host.closed) == 1


async def test_cache_payload_types_and_empty_replacement(contracts, tmp_path):
    specs = synthetic_specs(
        tmp_path,
        """Feature: Cache data
 Background:
  Given a fresh SDK acceptance test harness
  And the SDK clock is fixed at "2025-01-01T00:00:00Z"
  And persistent storage is empty
  And the mock PostHog server is reset
  And the SDK is initialized with token "test-token"
 Scenario Outline: Payload maps preserve values and presence
  Given cached feature flags are:
   | key | value | payload |
   | f | false | <payload> |
  When get feature flags and payloads is called
  Then the returned feature flag values should be:
   | key | value |
   | f | false |
  And the returned feature flag payloads should be:
   | key | payload |
   | f | <payload> |
  Given cached feature flags are empty
  When get feature flags and payloads is called
  Then the returned feature flag values should be empty
  And the returned feature flag payloads should be empty
  Examples:
   | payload |
   | false |
   | 0 |
   | "" |
   | {} |
   | [] |
   | null |
""",
    )
    async with serve(contracts, host_type=CachedFlagsHost) as (host, url):
        report, _ = await run(contracts, specs, [SYNTHETIC_FEATURE], url, host.profile["id"])
    assert strict_exit_code(contracts, report) == 0, report
    args = [entry["invoke"]["args"] for entry in host.inputs if entry["invoke"]["route"] == "/update_flags"]
    assert [type(a["payloads"]["f"]) for a in args[::2]] == [bool, int, str, dict, list, type(None)]
    assert all(a == {"flags": {}, "payloads": {}} for a in args[1::2])


@pytest.mark.parametrize("defect,code", [(None, 0), ("wrong_cached_value", 1)])
async def test_cached_cli_receipt_outside_checkout(contracts, tmp_path, defect, code):
    path = tmp_path / "cached.json"
    selected = identity("public/get-feature-flag", "L22")
    async with serve(contracts, host_type=CachedFlagsHost, defect=defect) as (host, url):
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("posthog-test-harness-v2")),
            "run",
            "--specs",
            str(SPECS),
            "--contracts",
            str(CONTRACT_PATH),
            "--all-features",
            "--case-id",
            selected,
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
