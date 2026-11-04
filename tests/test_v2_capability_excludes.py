"""`@excludes:<capability>` lets a declared capability replace a default case."""

import pytest

from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.gherkin import load_cases
from posthog_test_harness.v2.migration import selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_flush_host import serve
from tests.v2_remote_flags_host import RemoteFlagsHost

FEATURE = "migration/yaml-parity-v1/query-version.feature"
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


@pytest.fixture
def corpus(tmp_path):
    target = tmp_path / FEATURE
    target.parent.mkdir(parents=True)
    target.write_text(
        "@requires:flags_v2 @sdk:server\n"
        "Feature: Query version\n"
        "  Background:\n"
        "    Given an isolated SDK with empty persistent storage\n"
        "    And the mock PostHog server is reset\n"
        "    And the server uses its native flag startup and getter behavior"
        " with no installed local definitions or results\n"
        "  @excludes:flags_response_v4\n"
        "  Scenario: Default version\n"
        + REQUEST
        + '    And the first flags request query parameter "v" should equal "2"\n'
        "  @requires:flags_response_v4\n"
        "  Scenario: Replacement version\n"
        + REQUEST
        + '    And the first flags request query parameter "v" should equal "4"\n'
    )
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
    assert selection(replacement, declared, routes)["excluding_sdk_capabilities"] == []


async def test_explicitly_selected_excluded_case_is_a_visible_gap(corpus):
    contracts = Contracts()
    default = load_cases(corpus, [FEATURE])[0][0]
    capabilities = ("flags_v2", "flags_getter_remote_uncached", "flags_response_v4")
    async with serve(contracts, host_type=RemoteFlagsHost, sdk_capabilities=capabilities) as (host, url):
        report, _ = await run(contracts, corpus, [FEATURE], url, host.profile["id"], case_ids=[default.id])
    result = report["results"][0]["result"]
    assert result["status"] == "unsupported_binding"
    assert result["failure"]["code"] == "sdk_capability_excluded"
    assert not result["executed"] and not host.fixtures
    assert strict_exit_code(contracts, report) == 1
