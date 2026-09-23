"""Direct feature discovery and honest adapter/fixture applicability gaps."""

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.discovery import acceptance_paths, discover, execution_route, feature_paths
from posthog_test_harness.v2.gherkin import compile_feature, load_cases
from posthog_test_harness.v2.migration import migration_paths, selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from posthog_test_harness.v2.steps import Registry
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve


def test_discovery_from_features_without_catalog_or_ledger(specs):
    cases = discover(specs, migration_paths(specs))["cases"]
    assert len(cases) == 157
    assert len({c["case_id"] for c in cases}) == 157
    assert all(c["status"] == "harness_ready" for c in cases)
    assert all(c["required_fixture_capabilities"] == ["storage.empty.v1"] for c in cases)
    canonical = [p for p in feature_paths(specs) if p.startswith("acceptance/")]
    discovered = discover(specs, canonical)["cases"]
    parsed, _ = load_cases(specs, canonical)
    assert len(discovered) == len(parsed) > 0
    assert len({c["case_id"] for c in discovered}) == len(discovered)


def test_unlisted_local_feature_and_changed_content_are_executable_inputs(tmp_path):
    feature = tmp_path / "new.feature"
    feature.write_text("Feature: New\n Scenario: A\n  Given unbound text\n")
    first = discover(tmp_path)
    feature.write_text(feature.read_text() + "# editing needs no generated manifest\n")
    second = discover(tmp_path)
    assert first["inputs"] != second["inputs"]
    assert second["cases"][0]["status"] == "missing_harness"
    assert first["cases"][0]["case_id"] == second["cases"][0]["case_id"] == "new.feature::A"


def test_outline_case_tags_are_substituted_from_examples():
    text = """Feature: Outline
 @case:<case_id>
 Scenario Outline: case <n>
  Given input <n>
 @requires:capture_v0
 Examples:
  | n | case_id |
  | 1 | stable:first |
 @requires:capture_v1
 Examples:
  | n | case_id |
  | 2 | stable:second |
"""
    cases = compile_feature(text, "example.feature", "source")
    assert [c.id for c in cases] == ["stable:first", "stable:second"]
    assert "@requires:capture_v0" in cases[0].tags
    assert "@requires:capture_v1" in cases[1].tags
    assert [c.steps[0].text for c in cases] == ["input 1", "input 2"]


def test_migrated_scenario_uses_feature_path_and_name_without_a_case_tag():
    cases = compile_feature("Feature: F\n Scenario: S\n  Given step\n", "migration/yaml-parity-v1/a.feature", "source")
    assert [case.id for case in cases] == ["migration/yaml-parity-v1/a.feature::S"]


@pytest.mark.parametrize("tag", ["@case:<missing>", "@case:a @case:b"])
def test_malformed_or_ambiguous_case_tag_fails(tag):
    with pytest.raises(BoundaryError):
        compile_feature(f"Feature: F\n {tag}\n Scenario: S\n  Given step\n", "a.feature", "source")


def test_unlabeled_outline_rows_have_distinct_source_locations():
    cases = compile_feature(
        "Feature: F\n Scenario Outline: Matrix\n  Given input <n>\n Examples:\n  | n |\n  | 1 |\n  | 2 |\n",
        "a.feature",
        "source",
    )
    assert [case.id for case in cases] == ["a.feature::Matrix#L6", "a.feature::Matrix#L7"]


async def test_acceptance_tags_select_declared_sdk_type_without_legacy_scope_tags(tmp_path):
    feature = tmp_path / "acceptance/public/tags.feature"
    feature.parent.mkdir(parents=True)
    feature.write_text(
        """Feature: Scope
 @sdk:server
 Scenario: Server
  Given an isolated SDK instance
  When the SDK is initialized with token "test-token" and flush threshold 20
 @sdk:client
 Scenario: Client
  Given an isolated SDK instance
  When the SDK is initialized with token "test-token" and flush threshold 20
 @sdk:server @sdk:client
 Scenario: Both
  Given an isolated SDK instance
  When the SDK is initialized with token "test-token" and flush threshold 20
 @server @both
 Scenario: Legacy description only
  Given an isolated SDK instance
  When the SDK is initialized with token "test-token" and flush threshold 20
"""
    )
    paths = acceptance_paths(tmp_path)
    assert paths == ["acceptance/public/tags.feature"]
    for runtime, expected in (
        ("server", ["passed", "not_selected", "passed", "not_selected"]),
        ("browser", ["not_selected", "passed", "passed", "not_selected"]),
    ):
        async with serve(Contracts(), host_type=AIHost, runtime=runtime) as (host, url):
            report, _ = await run(Contracts(), tmp_path, paths, url, host.profile["id"], tagged_acceptance=True)
        assert [row["result"]["status"] for row in report["results"]] == expected, report
        assert len(host.fixtures) == 2
        assert strict_exit_code(Contracts(), report) == 0


def test_duplicate_case_selector_and_path_escape_fail(tmp_path):
    feature = tmp_path / "a.feature"
    feature.write_text("Feature: F\n @case:stable\n Scenario: S\n  Given step\n")
    for paths in (["a.feature", "a.feature"], ["../a.feature"]):
        with pytest.raises(BoundaryError):
            load_cases(tmp_path, paths)


def test_ambiguous_bindings_fail_discovery():
    registry = Registry()
    registry.step("step")(lambda: None)
    registry.step("step")(lambda: None)
    case = compile_feature("Feature: F\n Scenario: S\n  Given step\n", "a.feature", "source")[0]
    with pytest.raises(BoundaryError, match="Multiple"):
        execution_route(case, registry)


@pytest.mark.parametrize(
    "options,status,code",
    [
        ({"missing_route": "/capture_ai"}, "unsupported_binding", "missing_operation"),
        ({"missing_route": "/flush"}, "unsupported_binding", "missing_operation"),
        ({"missing_capability": "storage.empty.v1"}, "blocked_fixture", "fixture_unavailable"),
        ({"sdk_capabilities": []}, "unsupported_binding", "sdk_capability_unavailable"),
    ],
)
async def test_explicit_selection_keeps_genuine_gaps(options, status, code, specs):
    feature = "migration/yaml-parity-v1/capture-ai.feature"
    cases, _ = load_cases(specs, [feature])
    async with serve(Contracts(), host_type=AIHost, **options) as (host, url):
        report, _ = await run(Contracts(), specs, [feature], url, host.profile["id"], case_ids=[cases[0].id])
    result = report["results"][0]["result"]
    assert result["status"] == status
    assert result["failure"]["code"] == code
    assert not result["executed"] and not host.fixtures
    assert strict_exit_code(Contracts(), report) == 1


async def test_canonical_private_controls_are_reported_even_if_claimed(specs):
    feature = "acceptance/public/flush.feature"
    async with serve(Contracts(), host_type=AIHost) as (host, url):
        host.profile["fixture_capabilities"] += ["scheduler.manual.v1", "clock.fixed.v1", "queue.snapshot.v1"]
        report, _ = await run(Contracts(), specs, [feature], url, host.profile["id"])
    assert all(r["result"]["status"] == "blocked_fixture" for r in report["results"])
    assert not host.fixtures
    assert strict_exit_code(Contracts(), report) == 1


def test_routes_derived_from_bound_steps_not_tags(specs):
    cases, _ = load_cases(specs, ["migration/yaml-parity-v1/capture-ai.feature"])
    profile = {"sdk_capabilities": ["capture_ai_v0"]}
    assert selection(cases[0], profile, ["/setup", "/capture_ai", "/flush"])["selected"]
    assert selection(cases[0], profile, ["/setup", "/capture_ai"])["missing_routes"] == ["/flush"]
    assert not selection(cases[0], {"sdk_capabilities": []}, ["/setup", "/capture_ai", "/flush"])["selected"]


async def test_claimed_capture_with_missing_flush_is_selected_unsupported(specs):
    feature = "migration/yaml-parity-v1/capture-ai.feature"
    async with serve(Contracts(), host_type=AIHost, missing_route="/flush") as (host, url):
        report, _ = await run(Contracts(), specs, [feature], url, host.profile["id"])
    assert all(row["selected"] for row in report["inventory"])
    assert all(row["result"]["status"] == "unsupported_binding" for row in report["results"])
    assert not host.fixtures
    assert strict_exit_code(Contracts(), report) == 1
