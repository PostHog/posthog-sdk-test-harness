"""Gherkin bindings for local evaluation of config version 2 flags (sdk-specs #244 phrases)."""

import json

import pytest

from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_flush_host import serve
from tests.v2_local_evaluation_v2_host import LocalV2Host

FEATURE = "acceptance/private/local-evaluation-v2-steps.feature"
SECRET = '{"project_token":"phc_test_key","config":{"secret_key":"phx_test_key"}}'
LEGACY_ENTRY = {
    "id": 301,
    "key": "legacy-banner",
    "active": True,
    "version": 4,
    "filters": {"groups": [{"properties": [], "rollout_percentage": 100}]},
}
V2_ENTRY = {
    "id": 302,
    "key": "layout-preview",
    "active": True,
    "version": 7,
    "filters": {
        "version": 2,
        "return_type": "boolean",
        "default_value": False,
        "rules": [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "rule_type": "targeted_release",
                "targeting": {"properties": []},
                "value": True,
            }
        ],
    },
}
UNSUPPORTED_ENTRY = {
    "id": 303,
    "key": "future-config-version",
    "active": True,
    "version": 2,
    "filters": {"version": 3, "rules": []},
}
FLAGS_BODY = {
    "errorsWhileComputingFlags": False,
    "flags": {
        "future-config-version": {
            "key": "future-config-version",
            "value": True,
            "reason": {"code": "targeting_match", "condition_index": 0},
            "metadata": {
                "id": 303,
                "version": 2,
                "config_version": 2,
                "has_experiment": False,
                "payload": None,
                "rule_type": "targeted_release",
                "rule_id": "11111111-1111-4111-8111-111111111111",
            },
        }
    },
    "requestId": "20000000-0000-4000-8000-000000000001",
    "evaluatedAt": 1800000000000,
}


def doc(value):
    return '      """application/json\n      ' + json.dumps(value) + '\n      """\n'


def definitions(*flags, **extra):
    return {"definitions": {"flags": list(flags), "group_type_mapping": {}, "cohorts": {}}, **extra}


def getter(step, key):
    return f"    {step}\n" + doc({"key": key, "distinct_id": "person-example", "person_properties": {"plan": "pro"}})


BACKGROUND = (
    "  Background:\n    Given an isolated SDK with empty persistent storage\n    And the mock PostHog server is reset\n"
)
SCENARIOS = {
    "request": (
        "  Scenario: The SDK asks for the version 2 feed\n"
        "    Given the definitions service serves this typed document:\n"
        + doc(definitions(LEGACY_ENTRY))
        + "    And the definitions service serves this typed document for v=2 requests:\n"
        + doc(definitions(LEGACY_ENTRY, V2_ENTRY))
        + "    And the SDK is initialized for native local evaluation with JSON arguments:\n"
        + doc(json.loads(SECRET))
        + getter("When the local flag getter is called with JSON arguments:", "layout-preview")
        + "    Then the local flag getter should return JSON true\n"
        + "    When local definitions are publicly reloaded within 5000 milliseconds\n"
        + getter("And the local flag getter is called with JSON arguments:", "layout-preview")
        + "    Then the local flag getter should return JSON true\n"
        + '    And every flag definitions request query parameter "v" should equal "2"\n'
        + "    And no remote flag evaluation path should have been requested\n"
    ),
    "legacy_body": (
        "  Scenario: Without a v=2 document the version 2 feed is the legacy body\n"
        "    Given the definitions service serves this typed document:\n"
        + doc(definitions(LEGACY_ENTRY))
        + "    And the SDK is initialized for native local evaluation with JSON arguments:\n"
        + doc(json.loads(SECRET))
        + getter("When the local flag getter is called with JSON arguments:", "legacy-banner")
        + "    Then the local flag getter should return JSON true\n"
        + '    And every flag definitions request query parameter "v" should equal "2"\n'
    ),
    "fallback": (
        "  Scenario: An unsupported entry falls back to /flags\n"
        "    Given the definitions service serves this typed document for v=2 requests:\n"
        + doc(definitions(V2_ENTRY, UNSUPPORTED_ENTRY))
        + "    And the mock serves this /flags response body:\n"
        + doc(FLAGS_BODY)
        + "    And the SDK is initialized for native local evaluation with remote fallback and JSON arguments:\n"
        + doc(json.loads(SECRET))
        + getter("When get feature flag is called with JSON arguments:", "future-config-version")
        + "    Then the public flag getter should return exactly JSON true\n"
        + "    And exactly 1 requests containing /flags should have been received\n"
        + getter("When get feature flag is called with JSON arguments:", "layout-preview")
        + "    Then the public flag getter should return exactly JSON true\n"
        + "    And exactly 1 requests containing /flags should have been received\n"
    ),
    "native_fallback": (
        "  Scenario: The native setup still forbids /flags\n"
        "    Given the definitions service serves this typed document for v=2 requests:\n"
        + doc(definitions(V2_ENTRY, UNSUPPORTED_ENTRY))
        + "    And the mock serves this /flags response body:\n"
        + doc(FLAGS_BODY)
        + "    And the SDK is initialized for native local evaluation with JSON arguments:\n"
        + doc(json.loads(SECRET))
        + getter("When get feature flag is called with JSON arguments:", "future-config-version")
        + "    Then the public flag getter should return exactly JSON true\n"
    ),
    "events": (
        "  Scenario: A local read sends a config version 2 flag-called event\n"
        "    Given the definitions service serves this typed document for v=2 requests:\n"
        + doc(definitions(V2_ENTRY))
        + "    And the SDK is initialized for native local evaluation with JSON arguments:\n"
        + doc(json.loads(SECRET))
        + getter("When the local flag getter is called with JSON arguments:", "layout-preview")
        + "    And pending captures are flushed\n"
        + '    Then exactly 1 received events should be named "$feature_flag_called"\n'
        + '    And a received event named "$feature_flag_called" should have property "locally_evaluated"'
        + " equal to JSON true\n"
        + '    And no received event named "$feature_flag_called" should have property "$feature_flag_reason"\n'
    ),
    "no_events": (
        "  Scenario: The absence check needs a received event\n"
        "    Given the definitions service serves this typed document for v=2 requests:\n"
        + doc(definitions(V2_ENTRY))
        + "    And the SDK is initialized for native local evaluation with JSON arguments:\n"
        + doc(json.loads(SECRET))
        + '    Then no received event named "$feature_flag_called" should have property "$feature_flag_reason"\n'
    ),
    "poll": (
        "  Scenario: A conditional poll keeps v=2\n"
        "    Given the definitions service serves this typed document:\n"
        + doc(definitions(LEGACY_ENTRY, etag="legacy-feed"))
        + "    And the definitions service serves this typed document for v=2 requests:\n"
        + doc(definitions(LEGACY_ENTRY, V2_ENTRY, etag="v2-feed"))
        + "    And the SDK is initialized for native local evaluation with JSON arguments:\n"
        + doc(json.loads(SECRET))
        + getter("When the local flag getter is called with JSON arguments:", "layout-preview")
        + "    And local definitions are publicly reloaded within 5000 milliseconds and the definitions service"
        + " answers 304\n"
        + '    Then the definition request should include If-None-Match "v2-feed"\n'
        + '    And every flag definitions request query parameter "v" should equal "2"\n'
        + getter("When the local flag getter is called with JSON arguments:", "layout-preview")
        + "    Then the local flag getter should return JSON true\n"
    ),
}


@pytest.fixture
def corpus(tmp_path):
    target = tmp_path / FEATURE
    target.parent.mkdir(parents=True)
    target.write_text(
        "@requires:feature_flags_local_evaluation_v2\nFeature: Local evaluation v2 bindings\n"
        + BACKGROUND
        + "".join(SCENARIOS.values())
    )
    return tmp_path


def index(name):
    return list(SCENARIOS).index(name)


async def run_scenario(corpus, name, **options):
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host, **options) as (host, url):
        report, diagnostics = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    return report["results"][index(name)]["result"], diagnostics, host


@pytest.mark.parametrize("name", ["request", "legacy_body", "fallback", "events", "poll"])
async def test_a_v2_requesting_sdk_passes(corpus, name):
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host) as (host, url):
        report, _ = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    assert report["results"][index(name)]["result"]["status"] == "passed", report["results"][index(name)]


@pytest.mark.parametrize("defect", ["no_v", "v1", "repeated_v", "poll_drops_v"])
async def test_an_sdk_that_does_not_ask_for_v2_reads_the_legacy_body(corpus, defect):
    # The legacy body lacks the flag, so the local read after the defect is inconclusive.
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host, defect=defect, defect_case=index("request")) as (host, url):
        report, _ = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    result = report["results"][index("request")]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == "local_inconclusive"
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize("defect", ["no_v", "v1", "repeated_v"])
async def test_the_query_check_fails_when_only_the_legacy_body_is_read(corpus, defect):
    # Without a v=2 document both bodies are the same, so only the query check sees the defect.
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host, defect=defect, defect_case=index("legacy_body")) as (
        host,
        url,
    ):
        report, diagnostics = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    result = report["results"][index("legacy_body")]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == "definitions_request_query"
    details = diagnostics["cases"][index("legacy_body")]["failure"]["details"]
    expected = {"no_v": [], "v1": ["1"], "repeated_v": ["2", "2"]}[defect]
    assert details["parameter"] == "v" and details["expected"] == "2"
    assert [r["values"] for r in details["requests"]] == [expected]


async def test_the_query_check_needs_a_definitions_request(tmp_path):
    target = tmp_path / FEATURE
    target.parent.mkdir(parents=True)
    target.write_text(
        "Feature: No definitions request\n"
        + BACKGROUND
        + "  Scenario: Nothing loaded\n"
        + '    Then every flag definitions request query parameter "v" should equal "2"\n'
    )
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host) as (host, url):
        report, _ = await run(contracts, tmp_path, [FEATURE], url, host.profile["id"])
    result = report["results"][0]["result"]
    assert result["failure"]["code"] == "definitions_request_missing", result


async def test_the_native_setup_still_fails_a_fallback(corpus):
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host) as (host, url):
        report, diagnostics = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    result = report["results"][index("native_fallback")]["result"]
    assert result["status"] == "failed_assertion"
    assert result["failure"]["code"] == "local_remote_escape"
    fallback = report["results"][index("fallback")]["result"]
    assert fallback["status"] == "passed", fallback


@pytest.mark.parametrize("defect,code", [("v1_reason", "flag_event_property_present"), ("missing_reason_code", None)])
async def test_the_absent_property_check_reads_every_matching_event(corpus, defect, code):
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host, defect=defect, defect_case=index("events")) as (host, url):
        report, diagnostics = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    result = report["results"][index("events")]["result"]
    if code is None:
        assert result["status"] == "passed", result
        return
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    details = diagnostics["cases"][index("events")]["failure"]["details"]
    assert details == {
        "event": "$feature_flag_called",
        "property": "$feature_flag_reason",
        "events": 1,
        "with_property": [{"index": 0, "value": "Matched condition set 1"}],
    }


async def test_the_absent_property_check_needs_a_matching_event(corpus):
    result, _, _ = await run_scenario(corpus, "no_events")
    assert result["status"] == "failed_assertion"
    assert result["failure"]["code"] == "flag_event_missing"


async def test_a_conditional_poll_that_drops_v2_fails(corpus):
    contracts = Contracts()
    async with serve(contracts, host_type=LocalV2Host, defect="poll_drops_v", defect_case=index("poll")) as (
        host,
        url,
    ):
        report, _ = await run(contracts, corpus, [FEATURE], url, host.profile["id"])
    result = report["results"][index("poll")]["result"]
    assert result["status"] == "failed_assertion", result
    # The poll without v=2 sends the v=2 ETag to the legacy document and gets its 200 body.
    assert result["failure"]["code"] == "local_reload_not_modified"


async def test_a_v2_document_step_without_the_capability_is_unsupported(corpus):
    result, _, _ = await run_scenario(corpus, "request", sdk_capabilities=())
    assert result["status"] == "unsupported_binding"
    assert result["failure"]["code"] == "sdk_capability_unavailable"
