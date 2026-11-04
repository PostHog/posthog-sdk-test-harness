"""`@excludes:<capability>` lets a declared capability replace a default case."""

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_flush_host import serve
from tests.v2_remote_flags_host import RemoteFlagsHost

FEATURE = "migration/yaml-parity-v1/query-version.feature"
CANONICAL = "acceptance/public/query-version.feature"
REQUEST = '''    When the SDK is initialized with token "phc_test_key" and no additional configuration
    And get feature flag is called with JSON arguments:
      """application/json
      {
        "distinct_id": "user_1",
        "key": "any-flag"
      }
      """
    And exactly 1 requests containing /flags should have been received
'''


def write_feature(
    root, path, feature_tags="@requires:flags_v2 @sdk:server", default_tags="@excludes:flags_response_v4"
):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"{feature_tags}\n"
        "Feature: Query version\n"
        "  Background:\n"
        "    Given an isolated SDK with empty persistent storage\n"
        "    And the mock PostHog server is reset\n"
        "    And the server uses its native flag startup and getter behavior"
        " with no installed local definitions or results\n"
        f"  {default_tags}\n"
        "  Scenario: Default version\n"
        + REQUEST
        + '    And the first flags request query parameter "v" should equal "2"\n'
        "  @requires:flags_response_v4\n"
        "  Scenario: Replacement version\n"
        + REQUEST
        + '    And the first flags request query parameter "v" should equal "4"\n'
    )


@pytest.fixture
def corpus(tmp_path):
    write_feature(tmp_path, FEATURE)
    return tmp_path


def statuses(report):
    return [row["result"]["status"] for row in report["results"]]


@pytest.mark.parametrize(
    "capabilities,expected",
    [
        (("flags_v2", "flags_getter_remote_uncached"), ["passed", "not_selected"]),
        (("flags_v2", "flags_getter_remote_uncached", "flags_response_v4"), ["not_selected", "passed"]),
    ],
)
async def test_declared_capability_swaps_the_default_case(corpus, capabilities, expected):
    contracts = Contracts()
    async with serve(contracts, host_type=RemoteFlagsHost, sdk_capabilities=capabilities) as (host, url):
        report, diagnostics = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    assert statuses(report) == expected, report
    assert strict_exit_code(contracts, report) == 0
    excluded = [row for row in diagnostics["selection"] if row["excluding_sdk_capabilities"]]
    if "flags_response_v4" in capabilities:
        assert [row["excluding_sdk_capabilities"] for row in excluded] == [["flags_response_v4"]]
        assert excluded[0]["reason"] == "SDK declares a capability that replaces this case: flags_response_v4"
    else:
        assert not excluded


def test_exclusion_takes_precedence_over_missing_capabilities(corpus):
    default, replacement = load_cases(corpus, [FEATURE])[0]
    routes = ["/setup", "/get_feature_flag"]
    declared = {"sdk_capabilities": ["flags_response_v4"]}
    decision = selection(default, declared, routes)
    assert not decision["selected"]
    assert decision["missing_sdk_capabilities"] == ["flags_v2"]
    assert decision["excluding_sdk_capabilities"] == ["flags_response_v4"]
    assert decision["reason"].startswith("SDK declares a capability that replaces this case")
    assert decision["reason"] == selection(default, declared, [])["reason"]
    assert selection(replacement, declared, routes)["excluding_sdk_capabilities"] == []


V4 = ("flags_v2", "flags_getter_remote_uncached", "flags_response_v4")


@pytest.mark.parametrize(
    "capabilities,explicit,expected,exit_code",
    [
        # A replaced case alone does not make a successful run.
        (V4, [0], ["not_applicable", "not_selected"], 1),
        (V4, [0, 1], ["not_applicable", "passed"], 0),
        # The exclusion is reported before the capabilities the case is missing.
        (("flags_response_v4",), [0], ["not_applicable", "not_selected"], 1),
    ],
)
async def test_explicitly_selected_excluded_case_is_not_applicable(corpus, capabilities, explicit, expected, exit_code):
    contracts = Contracts()
    cases = load_cases(corpus, [FEATURE])[0]
    case_ids = [cases[index].id for index in explicit]
    async with serve(contracts, host_type=RemoteFlagsHost, sdk_capabilities=capabilities) as (host, url):
        report, diagnostics = await run(contracts, corpus, [FEATURE], url, host.profile["id"], case_ids=case_ids)
    assert statuses(report) == expected, report
    assert report["results"][0]["result"] == {
        "status": "not_applicable",
        "executed": False,
        "reason": "SDK declares a capability that replaces this case: flags_response_v4",
        "applicability_rule": "sdk-capability-excluded-v1",
    }
    assert report["inventory"][0]["applicability"]["rule"] == "sdk-capability-excluded-v1"
    assert diagnostics["cases"] == [] or [c["case_id"] for c in diagnostics["cases"]] == [cases[1].id]
    assert len(host.fixtures) == expected.count("passed")
    assert strict_exit_code(contracts, report) == exit_code


async def test_canonical_excluded_case_is_not_selected(tmp_path):
    write_feature(tmp_path, CANONICAL, feature_tags="@sdk:server")
    contracts = Contracts()
    capabilities = ("flags_v2", "flags_getter_remote_uncached", "flags_response_v4")
    async with serve(contracts, host_type=RemoteFlagsHost, sdk_capabilities=capabilities) as (host, url):
        report, _ = await run(contracts, tmp_path, [CANONICAL], url, host.profile["id"], tagged_acceptance=True)
    assert statuses(report) == ["not_selected", "passed"], report
    assert report["results"][0]["result"]["reason"].startswith("SDK declares a capability that replaces this case")
    assert strict_exit_code(contracts, report) == 0


@pytest.mark.parametrize(
    "feature_tags,default_tags",
    [
        ("@requires:flags_v2 @excludes:flags_response_v4", "@requires:flags_response_v4"),
        ("@requires:flags_v2", "@excludes:"),
    ],
)
def test_unsatisfiable_or_empty_exclusions_are_invalid_source(tmp_path, feature_tags, default_tags):
    write_feature(tmp_path, FEATURE, feature_tags=feature_tags, default_tags=default_tags)
    with pytest.raises(BoundaryError) as error:
        load_cases(tmp_path, [FEATURE])
    assert error.value.code == "invalid_source"
