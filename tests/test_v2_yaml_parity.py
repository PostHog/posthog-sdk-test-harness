"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

from copy import deepcopy

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments, utc_instant
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve

FEATURE = "migration/yaml-parity-v1/capture-ai.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


@pytest.mark.parametrize(
    "content,media", [('{"event":false,"event":true}', "application/json"), ("{}", "json"), ("[]", "application/json")]
)
def test_migration_argument_docstrings_are_strict(content, media, feature_cases):
    step = deepcopy(next(s for s in feature_cases[0].steps if "docString" in s.argument))
    step.argument["docString"].update(content=content, mediaType=media)
    with pytest.raises(BoundaryError):
        json_arguments(step)


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
async def test_migrated_assertions_reject_deliberate_defects(contracts, defect, index, code, specs):
    async with serve(contracts, host_type=AIHost, defect=defect, defect_case=index) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], timeout_ms=60000)
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
